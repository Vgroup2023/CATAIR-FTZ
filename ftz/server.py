"""HTTP server (standard library only): JSON API, printable forms, CSV export, static UI."""
import base64
import csv
import hmac
import html
import io
import json
import mimetypes
import os
import re
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import db, rules
from .service import ApiError, Service

STATIC = os.path.join(os.path.dirname(__file__), "static")
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
    add("POST", r"/api/admissions/(\d+)/(submit|approve|reject|delete)", lambda s, m, b, q: s.admission_action(int(m[1]), m[2], b))
    # inventory
    add("GET", "/api/lots", lambda s, m, b, q: s.list_lots(q.get("status"), q.get("zone_id"), q.get("stock") == "1"))
    add("GET", r"/api/lots/(\d+)/movements", lambda s, m, b, q: s.lot_movements(int(m[1])))
    add("POST", "/api/adjustments", lambda s, m, b, q: s.adjust(b))
    add("POST", "/api/withdrawals", lambda s, m, b, q: s.withdraw(b))
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
                         + grid([("line_no", "#"), ("description", "Description"), ("htsus", "HTSUS"), ("coo", "COO"),
                                 ("qty", "Qty"), ("uom", "UOM"), ("value", "Value (USD)"), ("zone_status", "Status"),
                                 ("duty_rate", "PF duty %")], sh["lines"]) + "</section>")
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
    else:
        raise ApiError("unknown form", 404)
    css = ("body{font:13px Arial,sans-serif;margin:24px}section{page-break-after:always;max-width:1000px}"
           "table{border-collapse:collapse;width:100%;margin:10px 0}th,td{border:1px solid #444;padding:4px 6px;text-align:left}"
           ".kv th{width:30%;background:#eee}.grid th{background:#ddd}h1{font-size:18px;margin:0}h2{font-size:14px;font-weight:400;margin:2px 0 10px}"
           ".note{font-size:11px;color:#555}")
    return (f"<!doctype html><meta charset=utf-8><title>Print</title><style>{css}</style>{''.join(parts)}"
            "<p class=note>Data worksheet generated by the standalone FTZ system. It is not an official CBP form; "
            "file through ACE or your broker/customs software.</p><script>window.print&&setTimeout(()=>print(),300)</script>")


def load_users():
    """Login accounts from FTZ_USERS="alice:pw1;bob:pw2" (or FTZ_AUTH_USER + FTZ_AUTH_PASSWORD)."""
    users = {}
    for pair in filter(None, (os.environ.get("FTZ_USERS") or "").split(";")):
        name, _, pw = pair.partition(":")
        if name.strip() and pw:
            users[name.strip()] = pw
    if os.environ.get("FTZ_AUTH_USER") and os.environ.get("FTZ_AUTH_PASSWORD"):
        users[os.environ["FTZ_AUTH_USER"]] = os.environ["FTZ_AUTH_PASSWORD"]
    return users


class Handler(BaseHTTPRequestHandler):
    server_version = "FTZ/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("FTZ_QUIET") != "1":
            sys.stderr.write("%s %s\n" % (self.command, self.path))

    def send(self, code, body, ctype="application/json", extra=None):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":  # HEAD gets the same headers, no body
            self.wfile.write(data)

    def json(self, code, obj):
        self.send(code, json.dumps(obj, default=str))

    def authenticate(self, users):
        """HTTP Basic auth; returns the username, or None after sending a 401."""
        try:
            kind, _, tok = (self.headers.get("Authorization") or "").partition(" ")
            name, _, pw = base64.b64decode(tok).decode().partition(":") if kind.lower() == "basic" else ("", "", "")
        except Exception:
            name = pw = ""
        expected = users.get(name)
        if expected is not None and hmac.compare_digest(expected.encode(), pw.encode()):
            return name
        time.sleep(0.5)  # slow down password guessing
        self.send(401, json.dumps({"errors": ["login required"]}), extra={"WWW-Authenticate": 'Basic realm="FTZ system", charset="UTF-8"'})
        return None

    def handle_any(self, method):
        url = urlparse(self.path)
        if url.path == "/healthz":
            return self.send(200, "ok", "text/plain")
        users = load_users()
        login = None
        if users:
            login = self.authenticate(users)
            if login is None:
                return
        path, query = url.path, {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if method == "GET" and not path.startswith(("/api/", "/print/", "/export/", "/backup")):
                return self.static(path)
            body = {}
            if method in ("POST", "PUT"):
                n = int(self.headers.get("Content-Length") or 0)
                if n > 5_000_000:
                    raise ApiError("request too large", 413)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    raise ApiError("invalid JSON")
                if not isinstance(body, dict):
                    raise ApiError("JSON object expected")
            user = login or (self.headers.get("X-User") or "unknown")[:60]  # logged-in name cannot be spoofed
            con = db.connect()
            try:
                svc = Service(con, user)
                if method == "GET" and path.startswith("/print/"):
                    m = re.match(r"^/print/(\w+)/(\d+)$", path)
                    if not m:
                        raise ApiError("not found", 404)
                    return self.send(200, print_page(m[1], svc, int(m[2])), "text/html; charset=utf-8")
                if method == "GET" and path.startswith("/export/"):
                    return self.export(con, path.split("/")[2].removesuffix(".csv"))
                if method == "GET" and path == "/backup":
                    return self.backup(con)
                for m, rx, fn in ROUTES:
                    mt = rx.match(path)
                    if m == method and mt:
                        con.execute("BEGIN IMMEDIATE")
                        try:
                            result = fn(svc, mt, body, query)
                            con.commit()
                        except BaseException:
                            con.rollback()
                            raise
                        return self.json(200, result)
                raise ApiError("not found", 404)
            finally:
                con.close()
        except ApiError as ex:
            self.json(ex.status, {"errors": ex.errors})
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
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        full = os.path.realpath(os.path.join(STATIC, rel))
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
    con.close()
    return ThreadingHTTPServer((host, port), Handler)


def main():
    host = os.environ.get("FTZ_HOST", "127.0.0.1")
    port = int(os.environ.get("FTZ_PORT") or os.environ.get("PORT") or 8214)
    if host not in ("127.0.0.1", "localhost", "::1") and not load_users():
        sys.exit("Refusing to listen on a public interface without a login. "
                 "Set FTZ_AUTH_USER and FTZ_AUTH_PASSWORD (or FTZ_USERS).")
    srv = make_server(host, port)
    print(f"FTZ system on http://{host}:{port}  (database: {db.db_path()})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
