"""Product master, WMS/ERP feeds and pre-submission validation. Mixed into Service."""
import hashlib
import re
import secrets
from urllib.parse import urlparse

from . import rules
from .common import ApiError, dec, need, pick, r4
from .db import audit, now, one, rows

EPS = 1e-4
AGENCY = re.compile(r"^[A-Z0-9]{2,10}$")


def find_token(con, token):
    """Return a user-like dict for a valid integration token, else None."""
    if not token or not token.startswith("ftz_"):
        return None
    t = one(con, "SELECT * FROM api_tokens WHERE token_hash=? AND active=1", (hashlib.sha256(token.encode()).hexdigest(),))
    if not t:
        return None
    con.execute("UPDATE api_tokens SET last_used=? WHERE id=?", (now(), t["id"]))
    con.commit()
    return {"id": 0, "username": f"api:{t['name']}", "role": "integration", "is_demo": 0, "active": 1, "email": None}


ERP_SYSTEMS = ["SAP S/4HANA", "SAP Business One", "Oracle NetSuite", "Oracle Fusion / E-Business Suite", "Microsoft Dynamics 365",
               "Microsoft Dynamics Business Central", "QuickBooks", "Odoo", "Sage Intacct", "Sage X3", "Infor", "Epicor", "Acumatica",
               "Other / in-house system"]
ERP_METHODS = {"api": "Secure API (push)", "csv": "CSV import / scheduled file", "manual": "Manual / not connected yet"}


def clean_https_url(u):
    """A link people will click: https only, no embedded password, no markup or whitespace."""
    u = str(u or "").strip()
    need(u and len(u) <= 500 and not re.search(r"[\s<>\"'\\\x00-\x1f]", u), "enter a web address such as https://erp.example.com")
    p = urlparse(u)
    need(p.scheme == "https" and p.hostname and not p.username and not p.password,
         "only secure https:// addresses (without a user name or password in them) are allowed")
    return u


class IntegrationMixin:
    # ------------------------------------------------------- client ERP connection links
    def list_erp_links(self):
        return rows(self.con, """SELECT e.*, p.name party_name FROM erp_links e LEFT JOIN parties p ON p.id=e.party_id
                                 ORDER BY e.active DESC, LOWER(e.client)""")

    def save_erp_link(self, d):
        if d.get("id"):                       # an update keeps every field the caller did not send
            cur = one(self.con, "SELECT * FROM erp_links WHERE id=?", (d["id"],))
            need(cur, "connection not found", 404)
            d = {**{k: cur[k] for k in ("client", "party_id", "system", "url", "method", "notes", "active")}, **d}
        party = None
        if d.get("party_id"):
            party = one(self.con, "SELECT id, name FROM parties WHERE id=?", (d["party_id"],))
            need(party, "that client does not exist")
        client = str(d.get("client") or "").strip() or (party["name"] if party else "")
        need(client and len(client) <= 120, "enter the client's name")
        system = str(d.get("system") or "").strip()
        need(system and len(system) <= 80, "choose or enter the ERP system")
        method = d.get("method") or "api"
        need(method in ERP_METHODS, "connection method must be api, csv or manual")
        notes = str(d.get("notes") or "").strip()
        need(len(notes) <= 1000, "notes are limited to 1000 characters")
        vals = (client, party["id"] if party else None, system, clean_https_url(d.get("url")), method, notes or None,
                0 if d.get("active") in (0, False, "0", "false") else 1, self.user, now())
        if d.get("id"):
            need(one(self.con, "SELECT id FROM erp_links WHERE id=?", (d["id"],)), "connection not found", 404)
            self.con.execute("""UPDATE erp_links SET client=?,party_id=?,system=?,url=?,method=?,notes=?,active=?,updated_by=?,updated_at=?
                                WHERE id=?""", vals + (d["id"],))
            lid = d["id"]
        else:
            lid = self.con.execute("""INSERT INTO erp_links(client,party_id,system,url,method,notes,active,updated_by,updated_at)
                                      VALUES(?,?,?,?,?,?,?,?,?)""", vals).lastrowid
        audit(self.con, self.user, "update" if d.get("id") else "create", "erp_link", lid, {"client": client, "system": system})
        return one(self.con, "SELECT * FROM erp_links WHERE id=?", (lid,))

    def delete_erp_link(self, lid):
        need(one(self.con, "SELECT id FROM erp_links WHERE id=?", (lid,)), "connection not found", 404)
        self.con.execute("DELETE FROM erp_links WHERE id=?", (lid,))
        audit(self.con, self.user, "delete", "erp_link", lid)
        return {"ok": True}

    # ------------------------------------------------------------ product master
    def _part(self, part_no):
        return one(self.con, "SELECT * FROM parts WHERE part_no=?", (part_no,)) if part_no else None

    def _apply_part_defaults(self, c):
        """Fill blanks on an e214 line from the product master (never overwrites what the line already says)."""
        p = self._part(c.get("part_no"))
        if not p or not p["active"]:
            return
        for f in ("description", "htsus", "coo", "uom"):
            if not c.get(f) and p[f]:
                c[f] = p[f]
        if not c.get("zone_status") and p["default_status"]:
            c["zone_status"] = p["default_status"]
        if c.get("duty_rate") is None and (c.get("zone_status") or "").upper() == "PF" and p["duty_rate"] is not None:
            c["duty_rate"] = p["duty_rate"]
        if p["uom2"] and not c.get("uom2"):
            c["uom2"] = p["uom2"]
        if c.get("uom2") and c.get("qty2") is None and p["conv"] and c.get("qty"):
            c["qty2"] = r4(dec(c["qty"]) * dec(p["conv"]))

    def upsert_part(self, d, source="manual"):
        part_no = str(d.get("part_no") or "").strip()
        need(part_no and len(part_no) <= 60, "part number is required")
        hts = rules.normalize_hts(d.get("htsus")) or None
        need(hts is None or rules.HTS_RE.match(hts), "HTSUS must be 10 digits (NNNN.NN.NNNN)")
        status = (d.get("default_status") or "").upper() or None
        need(status is None or status in rules.ZONE_STATUSES, "default status must be PF, NPF, D or ZR")
        conv, duty = rules.num(d.get("conv")), rules.num(d.get("duty_rate"))
        need(conv is None or conv > 0, "unit conversion must be greater than 0")
        need(duty is None or 0 <= duty <= 100, "duty rate must be 0-100")
        uom2 = (d.get("uom2") or "").strip() or None
        need(conv is None or uom2, "a conversion factor needs a customs unit")
        agencies = [a.strip().upper() for a in re.split(r"[,;\s]+", str(d.get("pga_agencies") or "")) if a.strip()]
        need(all(AGENCY.match(a) for a in agencies), "agency codes must be letters/numbers, e.g. FDA, USDA, EPA, CPSC, FCC")
        active = 0 if d.get("active") in (0, False, "0", "false") else 1
        mid = str(d.get("mid") or "").strip().upper() or None
        need(mid is None or re.fullmatch(r"[A-Z0-9]{1,22}", mid), "MID must be 1-22 letters/numbers (the Manufacturer Identification Code)")
        vals = (part_no, (d.get("description") or "").strip() or None, hts, (d.get("coo") or "").strip().upper() or None,
                (d.get("uom") or "").strip() or None, uom2, conv, duty, status, ",".join(agencies) or None, active, source, now(), mid)
        existing = self._part(part_no)
        if existing:
            self.con.execute("""UPDATE parts SET part_no=?,description=?,htsus=?,coo=?,uom=?,uom2=?,conv=?,duty_rate=?,default_status=?,
                                pga_agencies=?,active=?,source=?,updated_at=?,mid=? WHERE id=?""", vals + (existing["id"],))
        else:
            self.con.execute("""INSERT INTO parts(part_no,description,htsus,coo,uom,uom2,conv,duty_rate,default_status,pga_agencies,
                                active,source,updated_at,mid) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", vals)
        audit(self.con, self.user, "update" if existing else "create", "part", part_no, {"htsus": hts, "source": source})
        return self._part(part_no)

    def import_parts(self, d, source="import"):
        return self._import(d.get("rows"), lambda r: self.upsert_part(r, source), "part_no")

    def list_parts(self):
        return rows(self.con, "SELECT * FROM parts ORDER BY part_no")

    def _import(self, items, fn, label):
        need(isinstance(items, list) and items, "send a non-empty 'rows' list")
        need(len(items) <= 5000, "at most 5000 rows per import")
        ok, errs = 0, []
        for i, r in enumerate(items, 1):
            try:
                fn({str(k).strip().lower(): v for k, v in r.items()})
                ok += 1
            except ApiError as e:
                errs.append({"row": i, "key": (r.get(label) if isinstance(r, dict) else None), "error": "; ".join(e.errors)})
        return {"imported": ok, "errors": errs}

    # ---------------------------------------------------------------- WMS feeds
    def _receipt(self, r, source):
        no, pn = str(r.get("receipt_no") or "").strip(), str(r.get("part_no") or "").strip()
        need(no and pn, "receipt_no and part_no are required")
        q = rules.num(r.get("qty"))
        need(q is not None and q > 0, "qty must be greater than 0")
        on = r.get("received_on") or None
        need(on is None or rules.parse_date(on), "received_on must be YYYY-MM-DD")
        vals = (no, pn, q, (r.get("uom") or "").strip() or None, on, (r.get("ref") or "").strip() or None, source, now())
        self.con.execute("""INSERT INTO wms_receipts(receipt_no,part_no,qty,uom,received_on,ref,source,imported_at) VALUES(?,?,?,?,?,?,?,?)
                            ON CONFLICT(receipt_no,part_no) DO UPDATE SET qty=excluded.qty, uom=excluded.uom, received_on=excluded.received_on,
                            ref=excluded.ref, source=excluded.source, imported_at=excluded.imported_at""", vals)

    def import_receipts(self, d, source="import"):
        res = self._import(d.get("rows"), lambda r: self._receipt(r, source), "receipt_no")
        audit(self.con, self.user, "import", "wms_receipts", "-", {"imported": res["imported"], "errors": len(res["errors"])})
        return res

    def import_wms_inventory(self, d, source="import"):
        snap = d.get("snapshot_on") or now()[:10]
        need(rules.parse_date(snap), "snapshot_on must be YYYY-MM-DD")

        def one_row(r):
            pn, q = str(r.get("part_no") or "").strip(), rules.num(r.get("qty"))
            need(pn and q is not None and q >= 0, "part_no and qty (0 or more) are required")
            self.con.execute("""INSERT INTO wms_inventory(snapshot_on,part_no,qty,uom,source,imported_at) VALUES(?,?,?,?,?,?)
                                ON CONFLICT(snapshot_on,part_no) DO UPDATE SET qty=excluded.qty, uom=excluded.uom, imported_at=excluded.imported_at""",
                             (snap, pn, q, (r.get("uom") or "").strip() or None, source, now()))
        res = self._import(d.get("rows"), one_row, "part_no")
        audit(self.con, self.user, "import", "wms_inventory", snap, {"imported": res["imported"], "errors": len(res["errors"])})
        return {**res, "snapshot_on": snap}

    def list_wms(self):
        return {"receipts": rows(self.con, "SELECT * FROM wms_receipts ORDER BY id DESC LIMIT 500"),
                "inventory": rows(self.con, """SELECT * FROM wms_inventory WHERE snapshot_on=(SELECT MAX(snapshot_on) FROM wms_inventory)
                                               ORDER BY part_no""")}

    # ------------------------------------------------------------- API tokens
    def create_token(self, name):
        name = str(name or "").strip()
        need(re.match(r"^[A-Za-z0-9 ._-]{2,40}$", name), "token name must be 2-40 letters, numbers, spaces or . _ -")
        token = "ftz_" + secrets.token_urlsafe(32)
        cur = self.con.execute("INSERT INTO api_tokens(name,token_hash,created_by,created_at) VALUES(?,?,?,?)",
                               (name, hashlib.sha256(token.encode()).hexdigest(), self.user, now()))
        audit(self.con, self.user, "create", "api_token", cur.lastrowid, {"name": name})
        return {"id": cur.lastrowid, "name": name, "token": token}

    def list_tokens(self):
        return rows(self.con, "SELECT id,name,created_by,created_at,last_used,active FROM api_tokens ORDER BY id DESC")

    def revoke_token(self, tid):
        need(one(self.con, "SELECT id FROM api_tokens WHERE id=?", (tid,)), "token not found", 404)
        self.con.execute("UPDATE api_tokens SET active=0 WHERE id=?", (tid,))
        audit(self.con, self.user, "revoke", "api_token", tid)
        return {"ok": True}

    # -------------------------------------------------- pre-submission validation
    def _wms_findings(self, a):
        """Compare an e214's lines with the WMS receiving log for the same bill of lading."""
        if not one(self.con, "SELECT id FROM wms_receipts LIMIT 1"):
            return []                                     # no WMS data loaded: nothing to compare against
        bl = a.get("transport_doc") or ""
        recs = rows(self.con, "SELECT * FROM wms_receipts WHERE ref=?", (bl,))
        if not recs:
            return [f"No WMS receiving log found for B/L/AWB '{bl}': the goods may not have been received yet"]
        got, uoms = {}, {}
        for r in recs:
            k = r["part_no"].lower()
            got[k] = r4(dec(got.get(k, 0)) + dec(r["qty"]))
            uoms[k] = r["uom"]
        out, seen = [], set()
        for i, ln in enumerate(a["lines"], 1):
            pn = (ln.get("part_no") or "").lower()
            if not pn:
                continue
            seen.add(pn)
            if pn not in got:
                out.append(f"Line {i}: e214 lists {ln['part_no']} but the WMS has no receipt for it on this B/L")
                continue
            if abs(got[pn] - (ln["qty"] or 0)) > EPS:
                out.append(f"Line {i}: WMS received {got[pn]} of {ln['part_no']} but the e214 line says {ln['qty']}")
            if uoms[pn] and ln.get("uom") and uoms[pn].lower() != ln["uom"].lower():
                out.append(f"Line {i}: unit differs (WMS {uoms[pn]}, e214 {ln['uom']})")
        for k, q in got.items():
            if k not in seen:
                out.append(f"WMS received {q} of {k.upper()} on this B/L that is not on the e214")
        return out

    def admission_check(self, adm_id):
        a = self.get_admission(adm_id)
        errors, warnings = rules.validate_admission(a, a["lines"]), []
        for i, ln in enumerate(a["lines"], 1):
            pn = ln.get("part_no")
            if not pn:
                warnings.append(f"Line {i}: no part number, so it cannot be checked against the product master or WMS")
                continue
            p = self._part(pn)
            if not p:
                warnings.append(f"Line {i}: part {pn} is not in the product master")
                continue
            if not p["active"]:
                warnings.append(f"Line {i}: part {pn} is marked inactive in the product master")
            if p["htsus"] and ln["htsus"] and p["htsus"] != ln["htsus"]:
                warnings.append(f"Line {i}: HTSUS {ln['htsus']} differs from the product master ({p['htsus']})")
            if p["coo"] and ln["coo"] and p["coo"].upper() != ln["coo"].upper():
                warnings.append(f"Line {i}: country of origin {ln['coo']} differs from the product master ({p['coo']})")
            if p["pga_agencies"] and not ln.get("pga_ref"):
                warnings.append(f"Line {i}: {pn} is flagged for partner government agency review ({p['pga_agencies']}): enter the PGA reference or disclaimer")
            if p["conv"] and ln.get("qty2") is not None and ln["qty"]:
                exp = r4(dec(ln["qty"]) * dec(p["conv"]))
                if abs(exp - ln["qty2"]) > max(EPS, 0.0005 * exp):
                    warnings.append(f"Line {i}: customs quantity {ln['qty2']} does not match the master conversion ({exp} {p['uom2']})")
        wms = self._wms_findings(a)
        strict = self.settings().get("require_wms_match") == "1"
        (errors if strict else warnings).extend(("WMS mismatch: " + w) for w in wms)
        return {"ok": not errors, "errors": errors, "warnings": warnings, "wms_findings": len(wms), "wms_blocking": strict}
