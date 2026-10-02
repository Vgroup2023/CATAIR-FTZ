"""HTTP server (standard library only): JSON API, printable forms, CSV export, static UI."""
import csv
import html
import io
import json
import mimetypes
import os
import re
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import auth, db, integration, rules
from . import recon
from .auth import LIMITS
from .db import audit
from .demo import seed_demo_data
from .service import ApiError, Service

STATIC = os.path.join(os.path.dirname(__file__), "static")
mimetypes.add_type("application/manifest+json", ".webmanifest")
EXPORTS = {"admissions", "admission_lines", "lots", "movements", "permits", "activities",
           "inbonds", "inbond_lines", "audit"}


def routes():
    """(method, regex, handler(svc, match, body, query))"""
    R = []

    def add(method, pattern, fn):
        R.append((method, re.compile("^" + pattern + "$"), fn))

    add("GET", "/api/lookups", lambda s, m, b, q: s.lookups())
    add("GET", "/api/dashboard", lambda s, m, b, q: s.dashboard())
    add("GET", "/api/reports", lambda s, m, b, q: s.reports())
    add("GET", "/api/audit", lambda s, m, b, q: s.audit_log())
    add("PUT", "/api/settings", lambda s, m, b, q: s.set_settings(b))
    add("POST", "/api/zones", lambda s, m, b, q: s.create_zone(b))
    add("POST", "/api/parties", lambda s, m, b, q: s.create_party(b))
    # e214
    add("GET", "/api/admissions", lambda s, m, b, q: s.list_admissions(q.get("status")))
    add("POST", "/api/admissions", lambda s, m, b, q: s.save_admission(b))
    add("GET", r"/api/admissions/(\d+)", lambda s, m, b, q: s.get_admission(int(m[1])))
    add("PUT", r"/api/admissions/(\d+)", lambda s, m, b, q: s.save_admission(b, int(m[1])))
    add("GET", r"/api/admissions/(\d+)/check", lambda s, m, b, q: s.admission_check(int(m[1])))
    add("POST", r"/api/admissions/(\d+)/(submit|approve|reject|delete)", lambda s, m, b, q: s.admission_action(int(m[1]), m[2], b))
    # inventory
    add("GET", "/api/lots", lambda s, m, b, q: s.list_lots(q.get("status"), q.get("zone_id"), q.get("stock") == "1"))
    add("GET", r"/api/lots/(\d+)/movements", lambda s, m, b, q: s.lot_movements(int(m[1])))
    add("GET", "/api/stock", lambda s, m, b, q: s.stock_summary())
    add("POST", "/api/adjustments", lambda s, m, b, q: s.adjust(b))
    add("POST", "/api/withdrawals", lambda s, m, b, q: s.withdraw(b))
    add("POST", "/api/withdrawals/fifo", lambda s, m, b, q: s.withdraw_fifo(b))
    # product master, WMS feeds, reconciliation
    add("GET", "/api/parts", lambda s, m, b, q: s.list_parts())
    add("POST", "/api/parts", lambda s, m, b, q: s.upsert_part(b))
    add("POST", "/api/parts/import", lambda s, m, b, q: s.import_parts(b))
    add("GET", "/api/wms", lambda s, m, b, q: s.list_wms())
    add("POST", "/api/wms/receipts", lambda s, m, b, q: s.import_receipts(b))
    add("POST", "/api/wms/inventory", lambda s, m, b, q: s.import_wms_inventory(b))
    add("GET", "/api/recon", lambda s, m, b, q: s.recon_overview())
    add("POST", "/api/recon/run", lambda s, m, b, q: s.run_reconciliation("manual"))
    add("GET", "/api/integrity", lambda s, m, b, q: s.verify_integrity())
    # e216
    add("GET", "/api/permits", lambda s, m, b, q: s.list_permits())
    add("POST", "/api/permits", lambda s, m, b, q: s.save_permit(b))
    add("POST", r"/api/permits/(\d+)/(activate|revoke)", lambda s, m, b, q: s.permit_action(int(m[1]), m[2], b))
    add("GET", "/api/activities", lambda s, m, b, q: s.list_activities())
    add("POST", "/api/activities", lambda s, m, b, q: s.perform_activity(b))
    add("POST", r"/api/activities/(\d+)/return", lambda s, m, b, q: s.return_removal(int(m[1]), b))
    # in-bond
    add("GET", "/api/inbonds", lambda s, m, b, q: s.list_inbonds(q.get("type")))
    add("POST", "/api/inbonds", lambda s, m, b, q: s.create_inbond(b))
    add("GET", r"/api/inbonds/(\d+)", lambda s, m, b, q: s.get_inbond(int(m[1])))
    add("POST", r"/api/inbonds/(\d+)/(depart|arrive|close|cancel)", lambda s, m, b, q: s.inbond_action(int(m[1]), m[2], b))
    return R


ROUTES = routes()


def e(v):
    return html.escape("" if v is None else str(v))


def print_page(kind, svc, ident):
    """Worksheet layout mirroring the data elements of each form (not an official CBP form)."""
    def kv(pairs):
        return "<table class=kv>" + "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in pairs) + "</table>"

    def grid(cols, data):
        head = "".join(f"<th>{e(c[1])}</th>" for c in cols)
        body = "".join("<tr>" + "".join(f"<td>{e(r.get(c[0]))}</td>" for c in cols) + "</tr>" for r in data)
        return f"<table class=grid><tr>{head}</tr>{body}</table>"

    parts = []
    if kind == "admission":
        a = svc.get_admission(ident)
        for sh in a["sheets"]:
            names = {"214": "Application for FTZ Admission and/or Status Designation",
                     "214A": "FTZ Admission - Statistical (Census) Data",
                     "214B": "Continuation Sheet", "214C": "Continuation Sheet (Statistical)"}
            parts.append(f"<section><h1>CBP Form {sh['form']} worksheet</h1><h2>{names[sh['form']]} &mdash; sheet {sh['sheet']} of {sh['of']}</h2>"
                         + kv([("Document no.", a["doc_no"]), ("Status", a["status"]), ("CBP ref.", a["cbp_ref"]),
                               ("Zone", f"{a['zone_no']} {a['zone_name']}"), ("Operator", a["operator_name"]),
                               ("Importer of record", a["importer_name"]), ("Carrier", a["carrier_name"]),
                               ("Mode / B/L or AWB", f"{a['transport_mode'] or ''} / {a['transport_doc']}"),
                               ("Vessel / voyage", a["vessel_voyage"]), ("Port of entry", a["port_of_entry"]),
                               ("In-bond ref.", a["inbond_ref"]), ("Admission date", a["entry_date"])])
                         + grid([("line_no", "#"), ("part_no", "Part"), ("description", "Description"), ("htsus", "HTSUS"), ("coo", "COO"),
                                 ("qty", "Qty"), ("uom", "UOM"), ("qty2", "Customs qty"), ("uom2", "Customs UOM"), ("value", "Value (USD)"),
                                 ("zone_status", "Status"), ("duty_rate", "PF duty %"), ("pga_ref", "PGA ref.")], sh["lines"]) + "</section>")
    elif kind == "permit":
        p = svc.get_permit(ident)
        parts.append("<section><h1>CBP Form 216 worksheet</h1><h2>Application for FTZ Activity Permit</h2>" + kv([
            ("Permit no.", p["permit_no"]), ("Status", p["status"]), ("CBP ref.", p["cbp_ref"]),
            ("Zone", f"{p['zone_no']}"), ("Operator", p["operator_name"]),
            ("Type", rules.PERMIT_KINDS.get(p["kind"])),
            ("Activities", ", ".join(rules.ACTIVITIES[a] for a in p["activity_list"])),
            ("Valid", f"{p['valid_from']} to {p['valid_to']}"), ("Description", p["description"])]) + "</section>")
    elif kind == "inbond":
        b = svc.get_inbond(ident)
        parts.append(f"<section><h1>CBP Form 7512 worksheet &mdash; {e(b['type'])} (entry type {b['code']})</h1><h2>{e(b['type_name'])}</h2>" + kv([
            ("Document no.", b["doc_no"]), ("CBP in-bond no.", b["cbp_inbond_no"]), ("Status", b["status"]),
            ("Zone", b["zone_no"]), ("Carrier", b["carrier_name"]), ("Consignee", b["consignee_name"]),
            ("Surety / bond ref.", f"{b['surety_name'] or ''} {b['bond_ref'] or ''}"), ("Mode", b["mode"]),
            ("Origin port", b["origin_port"]), ("Destination port", b["dest_port"]), ("B/L or AWB", b["bl_no"]),
            ("Issued", b["issued_date"]), ("Due at destination", b["due_date"]), ("Arrived", b["arrival_date"]),
            ("Entry no. (IT)", b["entry_no"]), ("Export date", b["export_date"]),
            ("Exporting carrier", b["export_carrier"]), ("Foreign destination", b["foreign_dest"])])
            + grid([("line_no", "#"), ("lot_no", "Lot"), ("description", "Description"), ("htsus", "HTSUS"), ("qty", "Qty"),
                    ("uom", "UOM"), ("value", "Value"), ("zone_status", "Status")], b["lines"]) + "</section>")
    elif kind == "entry":
        w = svc.entry_worksheet(ident)
        for l in w["lines"]:
            l["rate"] = f"{round(100 * (l['duty'] or 0) / l['entered_value'], 3)}%" if l["entered_value"] else ""
        parts.append("<section><h1>CBP Form 3461 / 7501 data worksheet</h1><h2>Consumption entry from the foreign-trade zone</h2>" + kv([
            ("Entry number", w["entry_no"]), ("Entry type", "06 (FTZ consumption)"), ("Lines", len(w["lines"])),
            ("Total entered value (USD)", f"{w['total_value']:,.2f}"), ("Estimated duty (USD)", f"{w['total_duty']:,.2f}")])
            + grid([("admission_no", "e214"), ("admission_ref", "e214 CBP ref."), ("lot_no", "Lot"), ("part_no", "Part"),
                    ("description", "Description"), ("htsus", "HTSUS"), ("coo", "COO"), ("qty", "Qty"), ("uom", "UOM"),
                    ("qty2", "Customs qty"), ("uom2", "Customs UOM"), ("entered_value", "Entered value"), ("zone_status", "Status"),
                    ("rate", "Eff. duty rate"), ("duty", "Est. duty")], w["lines"]) + "</section>")
    else:
        raise ApiError("unknown form", 404)
    css = ("body{font:13px Manrope,Arial,sans-serif;margin:24px}.brandbar{display:flex;align-items:center;gap:12px;border-bottom:3px solid #d0a339;padding-bottom:8px;margin-bottom:12px}.brandbar img{height:44px;border-radius:6px}.brandbar b{font-size:15px;color:#5a3a16}section{page-break-after:always;max-width:1000px}"
           "table{border-collapse:collapse;width:100%;margin:10px 0}th,td{border:1px solid #444;padding:4px 6px;text-align:left}"
           ".kv th{width:30%;background:#f7efd9}.grid th{background:#f7efd9}h1{font-size:18px;margin:0}h2{font-size:14px;font-weight:400;margin:2px 0 10px}"
           ".note{font-size:11px;color:#555}")
    bar = "<div class=brandbar><img src=/compass.png alt=''><b>Globlexus Group &mdash; FTZ Control</b></div>"
    return (f"<!doctype html><meta charset=utf-8><title>Print</title><style>{css}</style>{bar}{''.join(parts)}"
            "<p class=note>Data worksheet generated by the standalone FTZ system. It is not an official CBP form; "
            "file through ACE or your broker/customs software.</p><script>window.print&&setTimeout(()=>print(),300)</script>")


# What an ERP/WMS integration token may call (everything else needs a signed-in person).
INTEGRATION_ALLOWED = [("POST", r"/api/wms/(receipts|inventory)"), ("POST", r"/api/parts(/import)?"), ("GET", r"/api/parts"),
                       ("POST", r"/api/admissions"), ("GET", r"/api/admissions/\d+/check"), ("GET", r"/api/lookups")]
# Files a browser needs before anyone signs in (look-and-feel plus what makes the app installable). None contain data.
PUBLIC_FILES = {"/style.css", "/auth.css", "/auth.js", "/pwa.js", "/logo.png", "/compass.png", "/favicon.png", "/manifest.webmanifest",
                "/sw.js", "/offline.html", "/icon-192.png", "/icon-512.png", "/icon-maskable-512.png", "/apple-touch-icon.png"}
COOKIE = "ftz_session"


def trust_proxy():
    return os.environ.get("FTZ_TRUST_PROXY", "").lower() in ("1", "true", "yes")


class Handler(BaseHTTPRequestHandler):
    server_version = "FTZ/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("FTZ_QUIET") != "1":
            sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))  # never log query strings (reset tokens)

    def send(self, code, body, ctype="application/json", extra=None, cookies=()):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        for c in cookies:
            self.send_header("Set-Cookie", c)
        self.end_headers()
        if self.command != "HEAD":  # HEAD gets the same headers, no body
            self.wfile.write(data)

    def json(self, code, obj, cookies=()):
        self.send(code, json.dumps(obj, default=str), cookies=cookies)

    # ---- request helpers
    def cookie_token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return None

    def client_ip(self):
        if trust_proxy() and self.headers.get("X-Forwarded-For"):
            return self.headers["X-Forwarded-For"].split(",")[0].strip()
        return self.client_address[0]

    def secure(self):
        return (trust_proxy() and self.headers.get("X-Forwarded-Proto") == "https") or \
            os.environ.get("FTZ_COOKIE_SECURE", "").lower() in ("1", "true", "yes")

    def base_url(self):
        return ("https" if self.secure() else "http") + "://" + (self.headers.get("Host") or "localhost")

    def session_cookie(self, token, max_age):
        return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}" + ("; Secure" if self.secure() else "")

    def read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 5_000_000:
            raise ApiError("request too large", 413)
        if n and not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
            raise ApiError("JSON requests only", 415)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise ApiError("invalid JSON")
        if not isinstance(body, dict):
            raise ApiError("JSON object expected")
        return body

    def tx(self, con, fn):
        con.execute("BEGIN IMMEDIATE")
        try:
            result = fn()
            con.commit()
            return result
        except BaseException:
            con.rollback()
            raise

    # ---- /auth/* (some public, some need a session)
    def auth_route(self, con, method, path, body, user):
        if method == "GET" and path == "/auth/config":
            return self.json(200, {"demo": auth.demo_credentials(), "email_reset": auth.smtp_ready()})
        if method == "POST" and path == "/auth/login":
            uname, ip = str(body.get("username") or "").strip().lower(), self.client_ip()
            key = f"u:{uname}|{ip}"
            if LIMITS.count(key, 900) >= 5 or LIMITS.count("ip:" + ip, 900) >= 30:
                raise ApiError("too many sign-in attempts - wait 15 minutes and try again", 429)
            u = auth.authenticate(con, body.get("username"), str(body.get("password") or ""))
            if not u:
                LIMITS.add(key)
                LIMITS.add("ip:" + ip)
                raise ApiError("incorrect user name or password", 401)
            LIMITS.clear(key)
            token = self.tx(con, lambda: auth.new_session(con, u["id"]))
            return self.json(200, {"user": auth.public_user(u)}, cookies=[self.session_cookie(token, auth.SESSION_HOURS * 3600)])
        if method == "POST" and path == "/auth/forgot":
            ident, ip = str(body.get("identifier") or "").strip().lower(), self.client_ip()
            if LIMITS.count("fip:" + ip, 900) < 5 and LIMITS.count("fid:" + ident, 3600) < 3:
                LIMITS.add("fip:" + ip)
                LIMITS.add("fid:" + ident)
                self.tx(con, lambda: auth.start_reset(con, ident, self.base_url()))
            return self.json(200, {"message": "If an account matches, a password reset link is on its way."})
        if method == "POST" and path == "/auth/reset":
            ip = self.client_ip()
            if LIMITS.count("rip:" + ip, 900) >= 10:
                raise ApiError("too many attempts - wait 15 minutes and try again", 429)
            LIMITS.add("rip:" + ip)
            name = self.tx(con, lambda: auth.complete_reset(con, str(body.get("token") or ""), str(body.get("password") or "")))
            return self.json(200, {"message": "Password updated. You can sign in now.", "username": name})
        if user is None:
            raise ApiError("sign in required", 401)
        if method == "GET" and path == "/auth/me":
            return self.json(200, auth.public_user(user))
        if method == "POST" and path == "/auth/logout":
            self.tx(con, lambda: auth.end_session(con, self.cookie_token()))
            return self.json(200, {"ok": True}, cookies=[self.session_cookie("", 0)])
        if method == "POST" and path == "/auth/password":
            self.tx(con, lambda: auth.change_own_password(con, user, str(body.get("current") or ""), str(body.get("new") or "")))
            token = self.tx(con, lambda: auth.new_session(con, user["id"]))  # the change signed everyone out; keep this browser in
            return self.json(200, {"message": "Password changed."}, cookies=[self.session_cookie(token, auth.SESSION_HOURS * 3600)])
        if method == "POST" and path == "/auth/email":
            self.tx(con, lambda: auth.update_own_email(con, user, str(body.get("current") or ""), body.get("email")))
            return self.json(200, {"message": "Email saved."})
        raise ApiError("not found", 404)

    def admin_route(self, con, method, path, body, user):
        """User management. Admins only; the shared demo account never qualifies."""
        if not (user["role"] == "admin" and not user["is_demo"]):
            raise ApiError("administrators only", 403)
        if path.startswith("/api/tokens"):
            svc = Service(con, user["username"])
            if method == "GET" and path == "/api/tokens":
                return self.json(200, svc.list_tokens())
            if method == "POST" and path == "/api/tokens":
                return self.json(200, self.tx(con, lambda: svc.create_token(body.get("name"))))   # the secret is shown only once
            m = re.match(r"^/api/tokens/(\d+)/revoke$", path)
            if method == "POST" and m:
                return self.json(200, self.tx(con, lambda: svc.revoke_token(int(m[1]))))
            raise ApiError("not found", 404)
        if method == "GET" and path == "/api/users":
            return self.json(200, auth.list_users(con))
        if method == "POST" and path == "/api/users":
            uid = self.tx(con, lambda: auth.create_user(con, body.get("username"), str(body.get("password") or ""), body.get("email"),
                                                        role=body.get("role") or "user"))
            self.tx(con, lambda: audit(con, user["username"], "create", "user", uid, {"username": body.get("username")}))
            return self.json(200, {"id": uid})
        m = re.match(r"^/api/users/(\d+)/(enable|disable|password)$", path)
        if method == "POST" and m:
            uid = int(m[1])
            if m[2] == "password":
                self.tx(con, lambda: auth.admin_set_password(con, user, uid, str(body.get("password") or "")))
            else:
                self.tx(con, lambda: auth.admin_set_active(con, user, uid, m[2] == "enable"))
            return self.json(200, {"ok": True})
        raise ApiError("not found", 404)

    def handle_any(self, method):
        url = urlparse(self.path)
        path, query = url.path, {k: v[0] for k, v in parse_qs(url.query).items()}
        if path == "/healthz":
            return self.send(200, "ok", "text/plain")
        try:
            body = {}
            if method in ("POST", "PUT"):
                origin = self.headers.get("Origin")
                if origin and urlparse(origin).netloc != self.headers.get("Host"):
                    raise ApiError("cross-site request blocked", 403)
                body = self.read_body()
            if method == "GET" and path in PUBLIC_FILES:
                return self.static(path)
            if method == "GET" and path == "/reset":
                return self.static("/reset.html")
            con = db.connect()
            try:
                bearer = self.headers.get("Authorization") or ""
                if bearer.startswith("Bearer "):
                    user = integration.find_token(con, bearer[7:].strip())
                    if not user:
                        raise ApiError("invalid or revoked API token", 401)
                    if not any(m == method and re.fullmatch(p, path) for m, p in INTEGRATION_ALLOWED):
                        raise ApiError("this API token cannot use that endpoint", 403)
                else:
                    user = auth.session_user(con, self.cookie_token())
                if path.startswith("/auth/"):
                    return self.auth_route(con, method, path, body, user)
                if method == "GET" and path == "/":
                    return self.static("/index.html" if user else "/login.html")
                if user is None:
                    if path.startswith("/api/") or path == "/app.js":
                        raise ApiError("sign in required", 401)
                    return self.send(302, "", "text/plain", {"Location": "/"})
                if method == "GET" and path == "/app.js":
                    return self.static("/app.js")
                if path.startswith(("/api/users", "/api/tokens")):
                    return self.admin_route(con, method, path, body, user)
                svc = Service(con, user["username"])
                if method == "GET" and path.startswith("/print/"):
                    m = re.match(r"^/print/(\w+)/([^/]+)$", path)
                    if not m:
                        raise ApiError("not found", 404)
                    ident = unquote(m[2])
                    if m[1] != "entry":
                        if not ident.isdigit():
                            raise ApiError("not found", 404)
                        ident = int(ident)
                    return self.send(200, print_page(m[1], svc, ident), "text/html; charset=utf-8")
                if method == "GET" and path.startswith("/export/"):
                    return self.export(con, path.split("/")[2].removesuffix(".csv"))
                if method == "GET" and path == "/backup":
                    if not (user["role"] == "admin" and not user["is_demo"]):
                        raise ApiError("administrators only", 403)  # the backup contains every account's password hash
                    return self.backup(con)
                for m, rx, fn in ROUTES:
                    mt = rx.match(path)
                    if m == method and mt:
                        return self.json(200, self.tx(con, lambda: fn(svc, mt, body, query)))
                raise ApiError("not found", 404)
            finally:
                con.close()
        except ApiError as ex:
            self.json(ex.status, {"errors": ex.errors})
        except sqlite3.IntegrityError as ex:     # a database safeguard refused the change (e.g. negative stock)
            self.json(409, {"errors": [f"Blocked by a database safeguard: {ex}"]})
        except Exception as ex:  # keep the server alive; details go to the log only
            sys.stderr.write(f"internal error: {ex!r}\n")
            self.json(500, {"errors": ["internal error - see server log"]})

    def export(self, con, table):
        if table not in EXPORTS:
            raise ApiError("unknown export", 404)
        cur = con.execute(f"SELECT * FROM {table}")
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([c[0] for c in cur.description])
        w.writerows(cur.fetchall())
        self.send(200, buf.getvalue(), "text/csv; charset=utf-8",
                  {"Content-Disposition": f'attachment; filename="{table}.csv"'})

    def backup(self, con):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            dst = os.path.join(d, "backup.db")
            out = sqlite3.connect(dst)
            con.backup(out)
            out.close()
            with open(dst, "rb") as fh:
                data = fh.read()
        self.send(200, data, "application/octet-stream", {"Content-Disposition": 'attachment; filename="ftz-backup.db"'})

    def static(self, path):
        full = os.path.realpath(os.path.join(STATIC, path.lstrip("/")))
        if not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full):
            return self.json(404, {"errors": ["not found"]})
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            data = fh.read()
        self.send(200, data, ctype + ("; charset=utf-8" if ctype.startswith("text") or "javascript" in ctype else ""))

    def do_GET(self): self.handle_any("GET")
    def do_HEAD(self): self.handle_any("GET")
    def do_POST(self): self.handle_any("POST")
    def do_PUT(self): self.handle_any("PUT")


def make_server(host="127.0.0.1", port=8214):
    con = db.connect()
    db.init(con)
    notes = auth.seed_users(con)
    if auth.demo_enabled() and seed_demo_data(con):
        notes.append("demo mode: sample data loaded")
    con.close()
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.notes = notes
    return srv


def main():
    host = os.environ.get("FTZ_HOST", "127.0.0.1")
    port = int(os.environ.get("FTZ_PORT") or os.environ.get("PORT") or 8214)
    srv = make_server(host, port)
    for n in srv.notes:
        print(n, flush=True)
    if os.environ.get("FTZ_RECON_AUTO", "1") != "0":     # daily reconciliation look-back
        stop = threading.Event()
        threading.Thread(target=recon.scheduler_loop, args=(stop, int(os.environ.get("FTZ_RECON_HOURS", "24"))), daemon=True).start()
    print(f"FTZ system on http://{host}:{port}  (database: {db.db_path()})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
