"""Two-factor sign-in: time-based one-time codes (RFC 6238, the kind Google Authenticator, Microsoft Authenticator, 1Password and Authy use)
plus one-time recovery codes. Standard library only."""
import base64
import hashlib
import hmac
import json
import re
import secrets
import struct
import time
from urllib.parse import quote

ISSUER = "Globlexus FTZ Control"
STEP = 30


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret, step):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    h = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = h[-1] & 15
    return f"{(struct.unpack('>I', h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def verify(secret, code, last_step=0, now=None, window=1):
    """Return the time step the code matched, or None. A step at or before `last_step` is refused, so a code works once."""
    code = re.sub(r"\s", "", str(code or ""))
    if not re.fullmatch(r"\d{6}", code):
        return None
    step = int((time.time() if now is None else now) // STEP)
    for s in range(step - window, step + window + 1):
        if s > int(last_step or 0) and hmac.compare_digest(code_at(secret, s), code):
            return s
    return None


def uri(account, secret):
    return f"otpauth://totp/{quote(ISSUER)}:{quote(account)}?secret={secret}&issuer={quote(ISSUER)}&algorithm=SHA1&digits=6&period={STEP}"


def pretty(secret):
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


def _h(code):
    return hashlib.sha256(re.sub(r"[^0-9a-f]", "", code.lower()).encode()).hexdigest()


def new_recovery_codes(n=8):
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(n)]
    return codes, json.dumps([_h(c) for c in codes])


def use_recovery(stored, code):
    """Return the remaining stored hashes if `code` is an unused recovery code, else None."""
    try:
        hashes = json.loads(stored or "[]")
    except ValueError:
        return None
    mine = _h(str(code or ""))
    if not re.fullmatch(r"[0-9a-f]{12}", re.sub(r"[^0-9a-f]", "", str(code or "").lower())):
        return None
    for h in hashes:
        if hmac.compare_digest(h, mine):
            hashes.remove(h)
            return json.dumps(hashes)
    return None
