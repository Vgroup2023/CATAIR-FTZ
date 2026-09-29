"""Business operations. Every mutating call is audited and runs in one transaction."""
import json

from . import rules
from .db import audit, next_no, now, one, rows


class ApiError(Exception):
    def __init__(self, errors, status=400):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        self.status = status
        super().__init__("; ".join(self.errors))


def need(cond, msg, status=400):
    if not cond:
        raise ApiError(msg, status)


def pick(d, keys):
    return {k: (d.get(k) if d.get(k) != "" else None) for k in keys}


def round_q(x):
    return round(float(x), 4)


class Service:
    def __init__(self, con, user="unknown"):
        self.con, self.user = con, user or "unknown"

    # ---------------------------------------------------------------- setup
    def settings(self):
        return {r["key"]: r["value"] for r in rows(self.con, "SELECT * FROM settings")}

    def set_settings(self, data):
        for k, v in data.items():
            need(k in rules.DEFAULT_SETTINGS, f"unknown setting {k}")
            need(str(v).isdigit() and int(v) > 0, f"{k} must be a positive whole number")
            self.con.execute("UPDATE settings SET value=? WHERE key=?", (str(v), k))
        audit(self.con, self.user, "update", "settings", "-", data)
        return self.settings()

    def _int(self, key):
        return int(self.settings()[key])

    def create_zone(self, d):
        need(d.get("zone_no") and d.get("name"), "zone number and name are required")
        try:
            cur = self.con.execute(
                "INSERT INTO zones(zone_no,name,grantee,port_code,address) VALUES(?,?,?,?,?)",
                (d["zone_no"].strip(), d["name"].strip(), d.get("grantee"), d.get("port_code"), d.get("address")))
        except Exception:
            raise ApiError("zone number already exists")
        audit(self.con, self.user, "create", "zone", cur.lastrowid, d)
        return one(self.con, "SELECT * FROM zones WHERE id=?", (cur.lastrowid,))

    def create_party(self, d):
        need(d.get("kind") in rules.PARTY_KINDS, "party type is invalid")
        need(str(d.get("name") or "").strip(), "name is required")
        cur = self.con.execute("INSERT INTO parties(kind,name,ident,address) VALUES(?,?,?,?)",
                               (d["kind"], d["name"].strip(), d.get("ident"), d.get("address")))
        audit(self.con, self.user, "create", "party", cur.lastrowid, d)
        return one(self.con, "SELECT * FROM parties WHERE id=?", (cur.lastrowid,))

    def _check_refs(self, h):
        for f, tbl in [("zone_id", "zones"), ("operator_id", "parties"), ("carrier_id", "parties"),
                       ("importer_id", "parties"), ("consignee_id", "parties"), ("surety_id", "parties")]:
            if h.get(f):
                need(one(self.con, f"SELECT id FROM {tbl} WHERE id=?", (h[f],)), f"{f} does not exist")

    # ----------------------------------------------------------- admissions (e214)
    ADM_FIELDS = ["zone_id", "operator_id", "importer_id", "carrier_id", "transport_mode", "transport_doc",
                  "vessel_voyage", "port_of_entry", "entry_date", "inbond_ref", "census_stat", "remarks"]
    LINE_FIELDS = ["description", "htsus", "coo", "qty", "uom", "value", "zone_status", "duty_rate", "marks", "location"]

    def _clean_lines(self, lines):
        out = []
        for ln in lines or []:
            c = pick(ln, self.LINE_FIELDS)
            c["zone_status"] = (c["zone_status"] or "").upper()
            c["htsus"] = rules.normalize_hts(c["htsus"]) or None
            c["coo"] = (c["coo"] or "").upper() or None
            for k in ("qty", "value", "duty_rate"):
                c[k] = rules.num(c[k])
            if c["zone_status"] != "PF":
                c["duty_rate"] = None
            out.append(c)
        return out

    def _save_lines(self, table, fk, parent_id, lines, extra=()):
        self.con.execute(f"DELETE FROM {table} WHERE {fk}=?", (parent_id,))
        for i, ln in enumerate(lines, 1):
            cols = ["line_no"] + list(ln.keys())
            self.con.execute(
                f"INSERT INTO {table}({fk},{','.join(cols)}) VALUES({','.join('?' * (len(cols) + 1))})",
                [parent_id, i] + list(ln.values()))

    def save_admission(self, d, adm_id=None):
        h = pick(d, self.ADM_FIELDS)
        h["census_stat"] = 1 if d.get("census_stat") in (True, 1, "1", "true", "on") else 0
        lines = self._clean_lines(d.get("lines"))
        self._check_refs(h)
        if adm_id:
            cur = one(self.con, "SELECT * FROM admissions WHERE id=?", (adm_id,))
            need(cur, "admission not found", 404)
            need(cur["status"] in ("draft", "rejected"), "only draft or rejected e214s can be edited")
            sets = ",".join(f"{k}=?" for k in h)
            self.con.execute(f"UPDATE admissions SET {sets},status='draft',updated_at=? WHERE id=?",
                             list(h.values()) + [now(), adm_id])
            action = "update"
        else:
            doc_no = next_no(self.con, "e214", now()[:4])
            cols = list(h) + ["doc_no", "status", "created_by", "created_at", "updated_at"]
            cur = self.con.execute(
                f"INSERT INTO admissions({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                list(h.values()) + [doc_no, "draft", self.user, now(), now()])
            adm_id, action = cur.lastrowid, "create"
        self._save_lines("admission_lines", "admission_id", adm_id, lines)
        audit(self.con, self.user, action, "admission", adm_id, {"lines": len(lines)})
        return self.get_admission(adm_id)

    def _adm_header_sql(self):
        return """SELECT a.*, z.zone_no, z.name zone_name, op.name operator_name, ca.name carrier_name,
                  im.name importer_name FROM admissions a
                  LEFT JOIN zones z ON z.id=a.zone_id LEFT JOIN parties op ON op.id=a.operator_id
                  LEFT JOIN parties ca ON ca.id=a.carrier_id LEFT JOIN parties im ON im.id=a.importer_id"""

    def get_admission(self, adm_id):
        a = one(self.con, self._adm_header_sql() + " WHERE a.id=?", (adm_id,))
        need(a, "admission not found", 404)
        a["lines"] = rows(self.con, "SELECT * FROM admission_lines WHERE admission_id=? ORDER BY line_no", (adm_id,))
        a["sheets"] = rules.sheet_layout(a["lines"], self._int("lines_per_sheet"), bool(a["census_stat"]))
        a["totals"] = {"lines": len(a["lines"]), "value": round(sum(l["value"] or 0 for l in a["lines"]), 2)}
        return a

    def list_admissions(self, status=None):
        sql = self._adm_header_sql() + (" WHERE a.status=?" if status else "") + " ORDER BY a.id DESC"
        out = rows(self.con, sql, (status,) if status else ())
        for a in out:
            t = one(self.con, "SELECT COUNT(*) n, COALESCE(SUM(value),0) v FROM admission_lines WHERE admission_id=?", (a["id"],))
            a["line_count"], a["total_value"] = t["n"], round(t["v"], 2)
        return out

    def admission_action(self, adm_id, action, d=None):
        d = d or {}
        a = self.get_admission(adm_id)
        st = a["status"]
        if action == "submit":
            need(st == "draft", "only drafts can be submitted")
            errs = rules.validate_admission(a, a["lines"])
            if errs:
                raise ApiError(errs)
            new = "submitted"
        elif action == "approve":
            need(st == "submitted", "only submitted e214s can be approved")
            need(str(d.get("cbp_ref") or "").strip(), "enter the CBP / ACE approval reference")
            new = "approved"
            self._create_lots(a)
            self.con.execute("UPDATE admissions SET cbp_ref=? WHERE id=?", (d["cbp_ref"].strip(), adm_id))
        elif action == "reject":
            need(st == "submitted", "only submitted e214s can be rejected")
            need(str(d.get("reason") or "").strip(), "enter a rejection reason")
            new = "rejected"
            self.con.execute("UPDATE admissions SET remarks=COALESCE(remarks||char(10),'')||? WHERE id=?",
                             ("REJECTED: " + d["reason"].strip(), adm_id))
        elif action == "delete":
            need(st in ("draft", "rejected"), "only draft or rejected e214s can be deleted")
            self.con.execute("DELETE FROM admission_lines WHERE admission_id=?", (adm_id,))
            self.con.execute("DELETE FROM admissions WHERE id=?", (adm_id,))
            audit(self.con, self.user, "delete", "admission", adm_id, {"doc_no": a["doc_no"]})
            return {"deleted": True}
        else:
            raise ApiError("unknown action", 404)
        self.con.execute("UPDATE admissions SET status=?, updated_at=? WHERE id=?", (new, now(), adm_id))
        audit(self.con, self.user, action, "admission", adm_id, {"doc_no": a["doc_no"], "status": new})
        return self.get_admission(adm_id)

    def _create_lots(self, a):
        for ln in a["lines"]:
            lot_no = f"{a['doc_no']}-L{ln['line_no']}"
            cur = self.con.execute(
                """INSERT INTO lots(lot_no,admission_id,line_id,zone_id,description,htsus,coo,uom,zone_status,
                   unit_value,duty_rate,qty_admitted,qty_on_hand,location,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (lot_no, a["id"], ln["id"], a["zone_id"], ln["description"], ln["htsus"], ln["coo"], ln["uom"],
                 ln["zone_status"], ln["value"] / ln["qty"], ln["duty_rate"], ln["qty"], ln["qty"],
                 ln["location"], now()))
            self._move(cur.lastrowid, "admit", ln["qty"], ln["value"], "e214", a["doc_no"])

    # ---------------------------------------------------------------- inventory
    def _move(self, lot_id, kind, qty, value, ref_type, ref_no, note=None, duty=None):
        self.con.execute(
            "INSERT INTO movements(ts,lot_id,kind,qty,value,ref_type,ref_no,note,user,duty) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (now(), lot_id, kind, qty, value, ref_type, ref_no, note, self.user, duty))

    def _lot(self, lot_id):
        lot = one(self.con, "SELECT * FROM lots WHERE id=?", (lot_id,))
        need(lot, "lot not found", 404)
        return lot

    def list_lots(self, status=None, zone_id=None, only_stock=False):
        sql = """SELECT l.*, z.zone_no FROM lots l LEFT JOIN zones z ON z.id=l.zone_id WHERE 1=1"""
        args = []
        if status:
            sql += " AND l.zone_status=?"; args.append(status)
        if zone_id:
            sql += " AND l.zone_id=?"; args.append(zone_id)
        if only_stock:
            sql += " AND (l.qty_on_hand>0 OR l.qty_out>0)"
        out = rows(self.con, sql + " ORDER BY l.id DESC", args)
        for l in out:
            l["value_on_hand"] = round(l["qty_on_hand"] * l["unit_value"], 2)
        return out

    def lot_movements(self, lot_id):
        return rows(self.con, "SELECT * FROM movements WHERE lot_id=? ORDER BY id", (lot_id,))

    def adjust(self, d):
        lot = self._lot(d.get("lot_id"))
        delta = rules.num(d.get("delta"))
        need(delta and delta != 0, "adjustment quantity must be non-zero")
        need(str(d.get("reason") or "").strip(), "a reason is required for inventory adjustments")
        need(lot["qty_on_hand"] + delta >= 0, "adjustment would make on-hand negative")
        self.con.execute("UPDATE lots SET qty_on_hand=? WHERE id=?", (round_q(lot["qty_on_hand"] + delta), lot["id"]))
        self._move(lot["id"], "adjust", delta, delta * lot["unit_value"], "adjustment", None, d["reason"])
        audit(self.con, self.user, "adjust", "lot", lot["id"], d)
        return self._lot(lot["id"])

    def withdraw(self, d):
        """Withdraw from the zone to U.S. consumption or to export (for in-bond use create_inbond)."""
        lot = self._lot(d.get("lot_id"))
        kind = d.get("kind")
        need(kind in ("consumption", "export", "transfer"), "kind must be consumption, export or transfer")
        q = rules.num(d.get("qty"))
        need(q and q > 0, "quantity must be greater than 0")
        need(q <= lot["qty_on_hand"] + 1e-9, f"only {lot['qty_on_hand']} {lot['uom']} on hand")
        date = d.get("date")
        need(rules.parse_date(date), "withdrawal date must be YYYY-MM-DD")
        duty, note = None, None
        if kind == "consumption":
            need(lot["zone_status"] != "ZR",
                 "zone-restricted merchandise may not enter U.S. commerce: export, destroy or move it in-bond (TE/IE)")
            need(str(d.get("entry_no") or "").strip(), "consumption entry number (CBP 7501) is required")
            rate = rules.num(d.get("duty_rate"))
            duty, err = rules.estimate_duty(lot, q, rate)
            need(err is None, err)
            note = f"entry {d['entry_no']}; est. duty {duty}"
        elif kind == "export":
            need(str(d.get("export_ref") or "").strip(), "export reference (AES ITN / booking) is required")
            note = f"export {d['export_ref']}"
        else:
            dest = one(self.con, "SELECT * FROM zones WHERE id=?", (d.get("dest_zone_id"),))
            need(dest, "select the destination zone")
            need(dest["id"] != lot["zone_id"], "destination zone must differ from the current zone")
            need(str(d.get("export_ref") or "").strip(), "transfer reference (e214 at destination / 7512) is required")
            note = f"transfer to {dest['zone_no']} ref {d['export_ref']}"
            new_no = f"{lot['lot_no']}-T{dest['id']}-{next_no(self.con, 'XFR', now()[:4])[-4:]}"
            cur = self.con.execute(
                """INSERT INTO lots(lot_no,admission_id,line_id,zone_id,description,htsus,coo,uom,zone_status,unit_value,
                   duty_rate,qty_admitted,qty_on_hand,location,parent_lot_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (new_no, lot["admission_id"], lot["line_id"], dest["id"], lot["description"], lot["htsus"], lot["coo"],
                 lot["uom"], lot["zone_status"], lot["unit_value"], lot["duty_rate"], q, q, None, lot["id"], now()))
            self._move(cur.lastrowid, "transfer_in", q, q * lot["unit_value"], "transfer", d["export_ref"], f"from {lot['lot_no']}")
        self.con.execute("UPDATE lots SET qty_on_hand=? WHERE id=?", (round_q(lot["qty_on_hand"] - q), lot["id"]))
        self._move(lot["id"], f"withdraw_{kind}" if kind != "transfer" else "transfer_out", -q, -q * lot["unit_value"],
                   "withdrawal", d.get("entry_no") or d.get("export_ref"), note, duty)
        audit(self.con, self.user, "withdraw", "lot", lot["id"], {**d, "duty": duty})
        return {"lot": self._lot(lot["id"]), "estimated_duty": duty}

    # ------------------------------------------------------------ permits (e216)
    def save_permit(self, d):
        p = pick(d, ["zone_id", "operator_id", "kind", "description", "valid_from", "valid_to"])
        p["activities"] = [a for a in (d.get("activities") or [])]
        errs = rules.validate_permit(p)
        if errs:
            raise ApiError(errs)
        self._check_refs(p)
        no = next_no(self.con, "e216", now()[:4])
        cur = self.con.execute(
            """INSERT INTO permits(permit_no,zone_id,operator_id,kind,activities,description,valid_from,valid_to,status,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (no, p["zone_id"], p["operator_id"], p["kind"], json.dumps(p["activities"]), p["description"],
             p["valid_from"], p["valid_to"], "draft", now()))
        audit(self.con, self.user, "create", "permit", cur.lastrowid, p)
        return self.get_permit(cur.lastrowid)

    def _permit_row(self, p):
        p["activity_list"] = json.loads(p["activities"] or "[]")
        if p["status"] == "active" and p["valid_to"] < rules.today().isoformat():
            p["status"] = "expired"
        return p

    def get_permit(self, pid):
        p = one(self.con, """SELECT p.*, z.zone_no, op.name operator_name FROM permits p
                LEFT JOIN zones z ON z.id=p.zone_id LEFT JOIN parties op ON op.id=p.operator_id WHERE p.id=?""", (pid,))
        need(p, "permit not found", 404)
        return self._permit_row(p)

    def list_permits(self):
        ids = [r["id"] for r in rows(self.con, "SELECT id FROM permits ORDER BY id DESC")]
        return [self.get_permit(i) for i in ids]

    def permit_action(self, pid, action, d=None):
        d = d or {}
        p = self.get_permit(pid)
        if action == "activate":
            need(p["status"] == "draft", "only draft permits can be activated")
            need(str(d.get("cbp_ref") or "").strip(), "enter the CBP approval reference for this e216")
            self.con.execute("UPDATE permits SET status='active', cbp_ref=? WHERE id=?", (d["cbp_ref"].strip(), pid))
        elif action == "revoke":
            need(p["status"] == "active", "only active permits can be revoked")
            self.con.execute("UPDATE permits SET status='revoked' WHERE id=?", (pid,))
        else:
            raise ApiError("unknown action", 404)
        audit(self.con, self.user, action, "permit", pid, {"permit_no": p["permit_no"]})
        return self.get_permit(pid)

    # ---------------------------------------------------------------- activities
    def perform_activity(self, d):
        kind = d.get("kind")
        need(kind in rules.ACTIVITIES, "activity must be manipulate, manufacture, exhibit, destroy or temp_removal")
        lot = self._lot(d.get("lot_id"))
        permit = self.get_permit(d.get("permit_id")) if d.get("permit_id") else None
        need(permit, "select the e216 permit that authorises this activity")
        ok, why = rules.permit_covers(permit, kind, d.get("performed_on"), lot["zone_id"])
        need(ok, "e216 check failed: " + why)
        q = rules.num(d.get("qty"))
        need(q and q > 0, "quantity must be greater than 0")
        need(q <= lot["qty_on_hand"] + 1e-9, f"only {lot['qty_on_hand']} {lot['uom']} on hand")
        act_no = next_no(self.con, "ACT", now()[:4])
        out_lot_id, out_qty, status = None, None, "done"
        loc = d.get("location") or None
        if kind == "manipulate":
            self.con.execute("UPDATE lots SET location=COALESCE(?,location), description=COALESCE(?,description) WHERE id=?",
                             (loc, d.get("output_desc") or None, lot["id"]))
            self._move(lot["id"], "manipulate", 0, 0, "e216", act_no, d.get("note"))
        elif kind == "exhibit":
            self.con.execute("UPDATE lots SET location=COALESCE(?,location) WHERE id=?", (loc, lot["id"]))
            self._move(lot["id"], "exhibit", 0, 0, "e216", act_no, d.get("note"))
        elif kind == "destroy":
            need(str(d.get("note") or "").strip(), "describe the destruction method / scrap or waste disposition")
            self.con.execute("UPDATE lots SET qty_on_hand=? WHERE id=?", (round_q(lot["qty_on_hand"] - q), lot["id"]))
            self._move(lot["id"], "destroy", -q, -q * lot["unit_value"], "e216", act_no, d.get("note"))
        elif kind == "temp_removal":
            need(rules.parse_date(d.get("expected_return")), "expected return date is required (YYYY-MM-DD)")
            need(d["expected_return"] >= d["performed_on"], "expected return cannot be before removal date")
            need(lot["zone_status"] != "ZR", "zone-restricted merchandise may not be removed for return to commerce")
            self.con.execute("UPDATE lots SET qty_on_hand=?, qty_out=? WHERE id=?",
                             (round_q(lot["qty_on_hand"] - q), round_q(lot["qty_out"] + q), lot["id"]))
            self._move(lot["id"], "temp_out", -q, -q * lot["unit_value"], "e216", act_no, d.get("note"))
            status = "open"
        elif kind == "manufacture":
            out_qty = rules.num(d.get("output_qty"))
            need(out_qty and out_qty > 0, "finished-goods quantity is required")
            need(str(d.get("output_desc") or "").strip(), "finished-goods description is required")
            oh = rules.normalize_hts(d.get("output_htsus"))
            need(rules.HTS_RE.match(oh), "finished-goods HTSUS (10 digits) is required")
            need(str(d.get("output_uom") or "").strip(), "finished-goods unit of measure is required")
            self.con.execute("UPDATE lots SET qty_on_hand=? WHERE id=?", (round_q(lot["qty_on_hand"] - q), lot["id"]))
            self._move(lot["id"], "manufacture_in", -q, -q * lot["unit_value"], "e216", act_no, d.get("note"))
            lot_no = f"{lot['lot_no']}-M{act_no[-4:]}"
            cur = self.con.execute(
                """INSERT INTO lots(lot_no,admission_id,line_id,zone_id,description,htsus,coo,uom,zone_status,unit_value,
                   duty_rate,qty_admitted,qty_on_hand,location,parent_lot_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (lot_no, lot["admission_id"], lot["line_id"], lot["zone_id"], d["output_desc"], oh, lot["coo"],
                 d["output_uom"], lot["zone_status"], q * lot["unit_value"] / out_qty, lot["duty_rate"],
                 out_qty, out_qty, loc or lot["location"], lot["id"], now()))
            out_lot_id = cur.lastrowid
            self._move(out_lot_id, "manufacture_out", out_qty, q * lot["unit_value"], "e216", act_no, d.get("note"))
        cur = self.con.execute(
            """INSERT INTO activities(act_no,permit_id,lot_id,kind,qty,output_qty,output_desc,output_htsus,output_lot_id,
               performed_on,location,note,expected_return,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (act_no, permit["id"], lot["id"], kind, q, out_qty, d.get("output_desc"), d.get("output_htsus"), out_lot_id,
             d["performed_on"], loc, d.get("note"), d.get("expected_return") or None, status, self.user, now()))
        audit(self.con, self.user, "perform", "activity", cur.lastrowid, {"act_no": act_no, "kind": kind, "lot": lot["lot_no"]})
        return one(self.con, "SELECT * FROM activities WHERE id=?", (cur.lastrowid,))

    def return_removal(self, act_id, d):
        a = one(self.con, "SELECT * FROM activities WHERE id=?", (act_id,))
        need(a and a["kind"] == "temp_removal", "temporary removal not found", 404)
        need(a["status"] == "open", "this removal is already closed")
        q = rules.num(d.get("qty"))
        outstanding = round_q(a["qty"] - a["returned_qty"])
        need(q and 0 < q <= outstanding + 1e-9, f"return quantity must be between 0 and {outstanding}")
        need(rules.parse_date(d.get("date")), "return date must be YYYY-MM-DD")
        lot = self._lot(a["lot_id"])
        self.con.execute("UPDATE lots SET qty_on_hand=?, qty_out=? WHERE id=?",
                         (round_q(lot["qty_on_hand"] + q), round_q(lot["qty_out"] - q), lot["id"]))
        self._move(lot["id"], "temp_in", q, q * lot["unit_value"], "e216", a["act_no"], d.get("note"))
        done = q >= outstanding - 1e-9
        self.con.execute("UPDATE activities SET returned_qty=?, returned_on=?, status=? WHERE id=?",
                         (round_q(a["returned_qty"] + q), d["date"], "returned" if done else "open", act_id))
        audit(self.con, self.user, "return", "activity", act_id, d)
        return one(self.con, "SELECT * FROM activities WHERE id=?", (act_id,))

    def list_activities(self):
        out = rows(self.con, """SELECT a.*, l.lot_no, l.uom, p.permit_no FROM activities a
                   LEFT JOIN lots l ON l.id=a.lot_id LEFT JOIN permits p ON p.id=a.permit_id ORDER BY a.id DESC""")
        t = rules.today().isoformat()
        for a in out:
            a["overdue"] = a["status"] == "open" and bool(a["expected_return"]) and a["expected_return"] < t
        return out

    # -------------------------------------------------------------- in-bond 7512
    def create_inbond(self, d):
        h = pick(d, ["type", "zone_id", "carrier_id", "consignee_id", "surety_id", "bond_ref", "mode", "origin_port",
                     "dest_port", "bl_no", "issued_date", "cbp_inbond_no", "remarks"])
        self._check_refs(h)
        raw = d.get("lines") or []
        lines = []
        for ln in raw:
            if ln.get("lot_id"):
                lot = self._lot(ln["lot_id"])
                q = rules.num(ln.get("qty"))
                need(q and q > 0, "line quantity must be greater than 0")
                need(h.get("zone_id") is None or lot["zone_id"] == int(h["zone_id"]), f"lot {lot['lot_no']} is in a different zone")
                lines.append({"lot_id": lot["id"], "description": lot["description"], "htsus": lot["htsus"],
                              "coo": lot["coo"], "qty": q, "uom": lot["uom"], "value": round(q * lot["unit_value"], 2),
                              "zone_status": lot["zone_status"]})
            else:
                c = self._clean_lines([ln])[0]
                lines.append({"lot_id": None, **{k: c[k] for k in ("description", "htsus", "coo", "qty", "uom", "value", "zone_status")}})
        errs = rules.validate_inbond(h, lines)
        if errs:
            raise ApiError(errs)
        # Stock check aggregated by lot, then deduct.
        want = {}
        for ln in lines:
            if ln["lot_id"]:
                want[ln["lot_id"]] = want.get(ln["lot_id"], 0) + ln["qty"]
        for lot_id, q in want.items():
            lot = self._lot(lot_id)
            need(q <= lot["qty_on_hand"] + 1e-9, f"lot {lot['lot_no']}: only {lot['qty_on_hand']} {lot['uom']} on hand")
        doc_no = next_no(self.con, f"IB-{h['type']}", now()[:4])
        due = rules.add_days(h["issued_date"], self._int(f"transit_days_{h['type']}"))
        cols = list(h) + ["doc_no", "due_date", "status", "created_by", "created_at"]
        cur = self.con.execute(f"INSERT INTO inbonds({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                               list(h.values()) + [doc_no, due, "issued", self.user, now()])
        ib_id = cur.lastrowid
        self._save_lines("inbond_lines", "inbond_id", ib_id, lines)
        for ln in lines:
            if ln["lot_id"]:
                lot = self._lot(ln["lot_id"])
                self.con.execute("UPDATE lots SET qty_on_hand=? WHERE id=?", (round_q(lot["qty_on_hand"] - ln["qty"]), lot["id"]))
                self._move(lot["id"], "withdraw_inbond", -ln["qty"], -ln["value"], "7512", doc_no, h["type"])
        audit(self.con, self.user, "create", "inbond", ib_id, {"doc_no": doc_no, "type": h["type"]})
        return self.get_inbond(ib_id)

    def _inbond_flags(self, ib):
        t = rules.today().isoformat()
        info = rules.INBOND_TYPES[ib["type"]]
        ib["code"], ib["type_name"] = info["code"], info["name"]
        ib["overdue"] = ib["status"] == "issued" and ib["due_date"] < t or ib["status"] == "in_transit" and ib["due_date"] < t
        ib["late_arrival"] = bool(ib["arrival_date"] and ib["arrival_date"] > ib["due_date"])
        return ib

    def get_inbond(self, ib_id):
        ib = one(self.con, """SELECT b.*, ca.name carrier_name, co.name consignee_name, su.name surety_name, z.zone_no
                 FROM inbonds b LEFT JOIN parties ca ON ca.id=b.carrier_id LEFT JOIN parties co ON co.id=b.consignee_id
                 LEFT JOIN parties su ON su.id=b.surety_id LEFT JOIN zones z ON z.id=b.zone_id WHERE b.id=?""", (ib_id,))
        need(ib, "in-bond not found", 404)
        ib["lines"] = rows(self.con, """SELECT il.*, l.lot_no FROM inbond_lines il LEFT JOIN lots l ON l.id=il.lot_id
                                       WHERE inbond_id=? ORDER BY line_no""", (ib_id,))
        ib["total_value"] = round(sum(l["value"] or 0 for l in ib["lines"]), 2)
        return self._inbond_flags(ib)

    def list_inbonds(self, type_=None):
        ids = rows(self.con, "SELECT id FROM inbonds" + (" WHERE type=?" if type_ else "") + " ORDER BY id DESC",
                   (type_,) if type_ else ())
        return [self.get_inbond(r["id"]) for r in ids]

    def inbond_action(self, ib_id, action, d=None):
        d = d or {}
        ib = self.get_inbond(ib_id)
        st = ib["status"]
        date = d.get("date")
        if action in ("depart", "arrive", "close"):
            need(rules.parse_date(date), "date must be YYYY-MM-DD")
            need(date >= ib["issued_date"], "date cannot be before the issue date")
        if action == "depart":
            need(st == "issued", "only issued in-bonds can be marked departed")
            self.con.execute("UPDATE inbonds SET status='in_transit' WHERE id=?", (ib_id,))
        elif action == "arrive":
            need(st in ("issued", "in_transit"), "in-bond has not left, or has already arrived")
            self.con.execute("UPDATE inbonds SET status='arrived', arrival_date=? WHERE id=?", (date, ib_id))
        elif action == "close":
            need(st == "arrived", "record arrival at the destination port before closing")
            if ib["type"] == "IT":
                need(str(d.get("entry_no") or "").strip(), "IT closes on consumption entry: enter the entry number")
                self.con.execute("UPDATE inbonds SET status='closed', closed_date=?, entry_no=? WHERE id=?",
                                 (date, d["entry_no"].strip(), ib_id))
            else:
                miss = [k for k in ("export_carrier", "foreign_dest") if not str(d.get(k) or "").strip()]
                need(not miss, f"{ib['type']} closes on proof of export: enter export carrier and foreign destination")
                self.con.execute("UPDATE inbonds SET status='closed', closed_date=?, export_date=?, export_carrier=?, foreign_dest=? WHERE id=?",
                                 (date, date, d["export_carrier"].strip(), d["foreign_dest"].strip(), ib_id))
        elif action == "cancel":
            need(st == "issued", "only in-bonds that have not departed can be cancelled")
            for ln in ib["lines"]:
                if ln["lot_id"]:
                    lot = self._lot(ln["lot_id"])
                    self.con.execute("UPDATE lots SET qty_on_hand=? WHERE id=?", (round_q(lot["qty_on_hand"] + ln["qty"]), lot["id"]))
                    self._move(lot["id"], "inbond_cancel", ln["qty"], ln["value"], "7512", ib["doc_no"], "cancelled")
            self.con.execute("UPDATE inbonds SET status='cancelled' WHERE id=?", (ib_id,))
        else:
            raise ApiError("unknown action", 404)
        audit(self.con, self.user, action, "inbond", ib_id, {"doc_no": ib["doc_no"], **d})
        return self.get_inbond(ib_id)

    # ---------------------------------------------------------------- reporting
    def dashboard(self):
        t = rules.today().isoformat()
        warn = rules.add_days(t, self._int("expiry_warning_days"))
        inb = self.list_inbonds()
        open_ib = [b for b in inb if b["status"] in ("issued", "in_transit", "arrived")]
        permits = self.list_permits()
        acts = self.list_activities()
        return {
            "admissions": {r["status"]: r["n"] for r in rows(self.con, "SELECT status, COUNT(*) n FROM admissions GROUP BY status")},
            "inventory": rows(self.con, """SELECT zone_status, COUNT(*) lots, ROUND(SUM(qty_on_hand*unit_value),2) value
                                          FROM lots WHERE qty_on_hand>0 GROUP BY zone_status"""),
            "inbond_open": {ty: sum(1 for b in open_ib if b["type"] == ty) for ty in rules.INBOND_TYPES},
            "inbond_overdue": [b for b in inb if b["overdue"]],
            "inbond_unclosed_arrived": [b for b in inb if b["status"] == "arrived"],
            "permits_expiring": [p for p in permits if p["status"] == "active" and p["valid_to"] <= warn],
            "permits_active": sum(1 for p in permits if p["status"] == "active"),
            "removals_open": [a for a in acts if a["status"] == "open"],
            "removals_overdue": [a for a in acts if a["overdue"]],
            "submitted_admissions": rows(self.con, "SELECT id, doc_no FROM admissions WHERE status='submitted'"),
        }

    def weekly_entries(self):
        """Consumption withdrawals grouped by week - the basis for a weekly entry filing."""
        return rows(self.con, """SELECT strftime('%Y-W%W', m.ts) week, l.zone_status, COUNT(*) withdrawals,
                   ROUND(-SUM(m.value),2) value, ROUND(SUM(m.duty),2) est_duty, GROUP_CONCAT(DISTINCT m.ref_no) entries
                   FROM movements m JOIN lots l ON l.id=m.lot_id WHERE m.kind='withdraw_consumption'
                   GROUP BY week, l.zone_status ORDER BY week DESC""")

    def reports(self):
        return {
            "weekly_entries": self.weekly_entries(),
            "inventory_reconciliation": rows(self.con, """SELECT z.zone_no, l.zone_status, ROUND(SUM(l.qty_admitted),4) admitted,
                   ROUND(SUM(l.qty_on_hand),4) on_hand, ROUND(SUM(l.qty_out),4) out_temp,
                   ROUND(SUM(l.qty_on_hand*l.unit_value),2) value FROM lots l JOIN zones z ON z.id=l.zone_id
                   GROUP BY z.zone_no, l.zone_status ORDER BY z.zone_no"""),
            "activity_summary": rows(self.con, "SELECT kind, COUNT(*) n, ROUND(SUM(qty),4) qty FROM activities GROUP BY kind"),
            "inbond_summary": rows(self.con, "SELECT type, status, COUNT(*) n FROM inbonds GROUP BY type, status ORDER BY type"),
        }

    def audit_log(self, limit=200):
        return rows(self.con, "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))

    def lookups(self):
        return {
            "zones": rows(self.con, "SELECT * FROM zones ORDER BY zone_no"),
            "parties": rows(self.con, "SELECT * FROM parties ORDER BY name"),
            "zone_statuses": rules.ZONE_STATUSES, "activities": rules.ACTIVITIES,
            "permit_kinds": rules.PERMIT_KINDS, "inbond_types": rules.INBOND_TYPES, "modes": rules.MODES,
            "party_kinds": rules.PARTY_KINDS, "settings": self.settings(),
        }
