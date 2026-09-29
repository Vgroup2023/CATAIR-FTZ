"""Accounts, sessions, password reset. Passwords are PBKDF2-hashed; tokens are stored only as SHA-256 hashes."""
import base64
import hashlib
import hmac
import os
import re
import secrets
import smtplib
import sys
import threading
import time
from datetime import datetime, timedelta
from email.message import EmailMessage

from .db import audit, now, one, rows
from .service import ApiError, need

SESSION_HOURS = 12
RESET_MINUTES = 30
COMMON = {"password", "password1", "password123", "1234567890", "12345678910", "qwertyuiop", "letmein123",
          "welcome123", "admin12345", "changeme123", "iloveyou123"}
DEMO_USER = "demo-admin"
DEMO_DEFAULT_PASSWORD = "Demo-FTZ-2026!"


def _b64(b):
    return base64.b64encode(b).decode()


def _iterations():
    return int(os.environ.get("FTZ_PBKDF2_ITER", "600000"))


def hash_password(pw):
    salt, it = os.urandom(16), _iterations()
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, it)
    return f"pbkdf2${it}${_b64(salt)}${_b64(dk)}"


def verify_password(pw, stored):
    try:
        _, it, salt, dk = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", pw.encode(), base64.b64decode(salt), int(it))
        return hmac.compare_digest(got, base64.b64decode(dk))
    except Exception:
        return False


_DUMMY = None


def _burn_time(pw):
    """Verify against a dummy hash so unknown usernames take as long as wrong passwords."""
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password("dummy-password-for-timing")
    verify_password(pw, _DUMMY)


def policy_errors(pw, username="", email=""):
    errs = []
    if len(pw or "") < 10:
        errs.append("password must be at least 10 characters")
    low = (pw or "").lower()
    if low in COMMON or len(set(low)) < 4:
        errs.append("password is too easy to guess")
    if username and username.lower() in low:
        errs.append("password must not contain the user name")
    if email and email.split("@")[0].lower() in low and len(email.split("@")[0]) > 2:
        errs.append("password must not contain your email name")
    return errs


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _iso(dt):
    return dt.isoformat(timespec="seconds")


class Limiter:
    """In-memory sliding-window counter (fine for the single-instance deployment)."""
    def __init__(self):
        self.h, self.lock = {}, threading.Lock()

    def count(self, key, window):
        t = time.time()
        with self.lock:
            q = [x for x in self.h.get(key, []) if t - x < window]
            self.h[key] = q
            return len(q)

    def add(self, key):
        with self.lock:
            self.h.setdefault(key, []).append(time.time())

    def clear(self, key):
        with self.lock:
            self.h.pop(key, None)


LIMITS = Limiter()


def valid_email(e):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e or "")) and len(e) <= 200


# ---------------------------------------------------------------- users
def create_user(con, username, password, email=None, role="user", is_demo=0, check_policy=True):
    username = (username or "").strip()
    need(re.match(r"^[A-Za-z0-9._-]{3,40}$", username), "user name must be 3-40 letters, numbers, . _ or -")
    need(role in ("admin", "user"), "role must be admin or user")
    email = (email or "").strip() or None
    need(email is None or valid_email(email), "email address looks invalid")
    if check_policy:
        errs = policy_errors(password, username, email or "")
        if errs:
            raise ApiError(errs)
    need(not one(con, "SELECT id FROM users WHERE username=?", (username,)), "that user name is taken")
    if email:
        need(not one(con, "SELECT id FROM users WHERE email=?", (email,)), "that email is already used by another account")
    cur = con.execute("INSERT INTO users(username,email,password_hash,role,is_demo,active,created_at) VALUES(?,?,?,?,?,1,?)",
                      (username, email, hash_password(password), role, int(is_demo), now()))
    return cur.lastrowid


def public_user(u):
    return {"id": u["id"], "username": u["username"], "email": u["email"], "role": u["role"],
            "is_demo": bool(u["is_demo"]), "active": bool(u["active"]), "last_login": u.get("last_login")}


def list_users(con):
    return [public_user(u) for u in rows(con, "SELECT * FROM users ORDER BY username")]


def authenticate(con, username, password):
    u = one(con, "SELECT * FROM users WHERE username=?", ((username or "").strip(),))
    if not u:
        _burn_time(password or "")
        return None
    if not verify_password(password or "", u["password_hash"]) or not u["active"]:
        return None
    return u


def new_session(con, user_id):
    con.execute("DELETE FROM sessions WHERE expires_at < ?", (now(),))
    token = secrets.token_urlsafe(32)
    con.execute("INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                (_digest(token), user_id, now(), _iso(datetime.now() + timedelta(hours=SESSION_HOURS))))
    con.execute("UPDATE users SET last_login=? WHERE id=?", (now(), user_id))
    return token


def session_user(con, token):
    if not token:
        return None
    return one(con, """SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
                       WHERE s.token_hash=? AND s.expires_at>? AND u.active=1""", (_digest(token), now()))


def end_session(con, token):
    con.execute("DELETE FROM sessions WHERE token_hash=?", (_digest(token or ""),))


def end_user_sessions(con, user_id):
    con.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))


def set_password(con, user, new_password, actor):
    errs = policy_errors(new_password, user["username"], user.get("email") or "")
    if errs:
        raise ApiError(errs)
    con.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_password), user["id"]))
    end_user_sessions(con, user["id"])
    audit(con, actor, "password_change", "user", user["id"], {"username": user["username"]})


def change_own_password(con, user, current, new):
    need(not user["is_demo"], "the demo account's password cannot be changed", 403)
    need(verify_password(current or "", user["password_hash"]), "current password is incorrect")
    set_password(con, user, new, user["username"])


def update_own_email(con, user, current, email):
    need(not user["is_demo"], "the demo account cannot be edited", 403)
    need(verify_password(current or "", user["password_hash"]), "current password is incorrect")
    email = (email or "").strip() or None
    need(email is None or valid_email(email), "email address looks invalid")
    need(not email or not one(con, "SELECT id FROM users WHERE email=? AND id<>?", (email, user["id"])), "that email is already used by another account")
    con.execute("UPDATE users SET email=? WHERE id=?", (email, user["id"]))
    audit(con, user["username"], "email_change", "user", user["id"])


def admin_set_active(con, actor, uid, active):
    u = one(con, "SELECT * FROM users WHERE id=?", (uid,))
    need(u, "user not found", 404)
    need(not u["is_demo"], "the demo account is managed by server settings", 403)
    if not active:
        need(u["id"] != actor["id"], "you cannot disable your own account")
        others = one(con, "SELECT COUNT(*) n FROM users WHERE role='admin' AND active=1 AND is_demo=0 AND id<>?", (uid,))["n"]
        need(u["role"] != "admin" or others > 0, "at least one active admin must remain")
    con.execute("UPDATE users SET active=? WHERE id=?", (1 if active else 0, uid))
    if not active:
        end_user_sessions(con, uid)
    audit(con, actor["username"], "enable" if active else "disable", "user", uid, {"username": u["username"]})


def admin_set_password(con, actor, uid, password):
    u = one(con, "SELECT * FROM users WHERE id=?", (uid,))
    need(u, "user not found", 404)
    need(not u["is_demo"], "the demo account's password is managed by server settings", 403)
    set_password(con, u, password, actor["username"])


# ------------------------------------------------------------ password reset
def smtp_ready():
    return bool(os.environ.get("FTZ_SMTP_HOST") and os.environ.get("FTZ_BASE_URL"))


def _send_mail(to, subject, text):
    def run():
        try:
            msg = EmailMessage()
            msg["From"] = os.environ.get("FTZ_SMTP_FROM") or os.environ.get("FTZ_SMTP_USER") or "no-reply@localhost"
            msg["To"], msg["Subject"] = to, subject
            msg.set_content(text)
            with smtplib.SMTP(os.environ["FTZ_SMTP_HOST"], int(os.environ.get("FTZ_SMTP_PORT", "587")), timeout=20) as s:
                s.starttls()
                if os.environ.get("FTZ_SMTP_USER"):
                    s.login(os.environ["FTZ_SMTP_USER"], os.environ.get("FTZ_SMTP_PASSWORD", ""))
                s.send_message(msg)
        except Exception as ex:
            sys.stderr.write(f"could not send reset email: {ex!r}\n")
    threading.Thread(target=run, daemon=True).start()


def start_reset(con, identifier, request_base):
    """Always returns quietly, so callers cannot learn whether an account exists."""
    ident = (identifier or "").strip()
    if not ident:
        return
    u = one(con, "SELECT * FROM users WHERE (username=? OR email=?) AND active=1 AND is_demo=0", (ident, ident))
    if not u:
        return
    token = secrets.token_urlsafe(32)
    con.execute("UPDATE reset_tokens SET used=1 WHERE user_id=? AND used=0", (u["id"],))
    con.execute("INSERT INTO reset_tokens(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                (_digest(token), u["id"], now(), _iso(datetime.now() + timedelta(minutes=RESET_MINUTES))))
    audit(con, u["username"], "reset_requested", "user", u["id"])
    if smtp_ready() and u["email"]:
        link = os.environ["FTZ_BASE_URL"].rstrip("/") + "/reset?token=" + token
        _send_mail(u["email"], "Reset your Globlexus FTZ password",
                   f"Hello {u['username']},\n\nUse this link within {RESET_MINUTES} minutes to choose a new password:\n{link}\n\n"
                   "If you did not ask for this, ignore this email - your password has not changed.\n")
    elif not smtp_ready():
        # No email configured: the link goes to the server log, which only the site owner can read.
        sys.stderr.write(f"PASSWORD RESET LINK for '{u['username']}' (valid {RESET_MINUTES} min): {request_base}/reset?token={token}\n")


def complete_reset(con, token, new_password):
    t = one(con, "SELECT * FROM reset_tokens WHERE token_hash=? AND used=0 AND expires_at>?", (_digest(token or ""), now()))
    need(t, "this reset link is invalid or has expired - request a new one")
    u = one(con, "SELECT * FROM users WHERE id=? AND active=1 AND is_demo=0", (t["user_id"],))
    need(u, "this reset link is invalid or has expired - request a new one")
    set_password(con, u, new_password, u["username"])
    con.execute("UPDATE reset_tokens SET used=1 WHERE user_id=?", (u["id"],))
    return u["username"]


# ------------------------------------------------------------------ seeding
def demo_enabled():
    return os.environ.get("FTZ_DEMO_MODE", "").lower() in ("1", "true", "yes", "on")


def demo_credentials():
    return {"username": DEMO_USER, "password": os.environ.get("FTZ_DEMO_PASSWORD") or DEMO_DEFAULT_PASSWORD} if demo_enabled() else None


def seed_users(con):
    """Create accounts from environment variables (only if missing) and manage the demo account."""
    notes = []
    env_users = {}
    for pair in filter(None, (os.environ.get("FTZ_USERS") or "").split(";")):
        name, _, pw = pair.partition(":")
        if name.strip() and pw:
            env_users[name.strip()] = (pw, None)
    if os.environ.get("FTZ_AUTH_USER") and os.environ.get("FTZ_AUTH_PASSWORD"):
        env_users[os.environ["FTZ_AUTH_USER"]] = (os.environ["FTZ_AUTH_PASSWORD"], os.environ.get("FTZ_AUTH_EMAIL"))
    first = True
    for name, (pw, email) in env_users.items():
        if not one(con, "SELECT id FROM users WHERE username=?", (name,)):
            try:
                create_user(con, name, pw, email, role="admin" if first else "user", check_policy=False)
                notes.append(f"created account '{name}' from environment settings")
            except ApiError as e:
                notes.append(f"could not create '{name}': {e}")
        first = False
    d = demo_credentials()
    existing = one(con, "SELECT * FROM users WHERE username=?", (DEMO_USER,))
    if d:
        if existing:
            con.execute("UPDATE users SET password_hash=?, active=1, is_demo=1, role='admin' WHERE id=?", (hash_password(d["password"]), existing["id"]))
        else:
            create_user(con, DEMO_USER, d["password"], None, role="admin", is_demo=1, check_policy=False)
        notes.append("demo mode ON: demo login is shown on the front page")
    elif existing:
        con.execute("UPDATE users SET active=0 WHERE id=?", (existing["id"],))
        end_user_sessions(con, existing["id"])
    if not one(con, "SELECT id FROM users WHERE is_demo=0 AND active=1"):
        pw = secrets.token_urlsafe(12)
        create_user(con, "admin", pw, None, role="admin", check_policy=False)
        notes.append(f"NO ACCOUNTS EXISTED - created user 'admin' with one-time password: {pw}  (change it under My Account)")
    con.commit()
    return notes
