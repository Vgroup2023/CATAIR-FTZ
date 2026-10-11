"""CATAIR FT (Input) filings: save, check, prefill from an e214 admission, export. Mixed into Service."""
import hashlib
import json
import re

from . import catair, catair_fz
from .common import ApiError, need
from .db import audit, now, one, rows

MAX_DATA_BYTES = 4_000_000
IOR_ANY = re.compile(r"\b(\d{2}-\d{9}|\d{3}-\d{2}-\d{4})\b")
ABI_KEYS = ("site_code", "sender_id", "office_code", "filer_code", "port_code")


KINDS = ("FT", "FZ")
MAX_RESPONSE = 300_000
STATUS_RANK = {"draft": 0, "ready": 1, "exported": 2, "rejected": 3, "accepted": 4, "authorized": 5}


def compile_any(kind, data):
    return catair_fz.compile_fz(data) if kind == "FZ" else catair.compile_filing(data)


def empty_data():
    return {"header": {"action": "A", "direct_delivery": "N"}, "replace": {"reason_codes": [], "remarks": []}, "conveyances": [], "delete_remarks": []}


class FtFilingMixin:
    # ------------------------------------------------------------------ settings and spec
    def abi_profile(self):
        st = self.settings()
        return {k: st.get("abi_" + k, "") for k in ABI_KEYS}

    def ft_spec(self):
        return {"source": catair.SOURCE, "layout": catair.layout_summary(), "maps": {k: {"title": v[0], "rows": v[1]} for k, v in catair.USAGE_MAPS.items()},
                "fz": {"layout": {k: {"title": v["title"], "fields": [{kk: vv for kk, vv in f.items() if kk in ("key", "label", "start", "end", "len", "cls", "req", "help", "choices", "date")}
                                                                      for f in v["fields"]]} for k, v in catair_fz.FZ_LAYOUT.items()},
                       "actions": {k: {"title": v[0], "qualifiers": v[1]} for k, v in catair_fz.FZ_ACTIONS.items()}, "reasons": catair_fz.PAC_REASONS,
                       "map": {"title": catair_fz.FZ_MAP[0], "rows": catair_fz.FZ_MAP[1]}, "limits": catair_fz.FZ_LIMITS},
                "nf": {"scenarios": [{"disposition": k[0], "type": k[1], "code": k[2], "text": v[0], "outcome": v[1]} for k, v in catair_fz.NF_SCENARIOS.items()]},
                "limits": catair.LIMITS, "admission_types": catair.ADMISSION_TYPES, "reasons": catair.REASONS, "abi": self.abi_profile(),
                "ruler": "".join(str((i // 10) % 10) if i % 10 == 0 else "." for i in range(1, 81))}

    # ------------------------------------------------------------------ storage
    def _ft_row(self, fid):
        r = one(self.con, "SELECT * FROM ft_filings WHERE id=?", (fid,))
        need(r, "filing not found", 404)
        return r

    def _ft_public(self, r, with_data=True):
        out = {k: r[k] for k in ("id", "kind", "label", "admission_id", "action", "admission_number", "status", "created_by", "created_at",
                                 "updated_by", "updated_at", "exported_at")}
        if with_data:
            out["data"] = json.loads(r["data"])
        return out

    def ft_list(self):
        return [self._ft_public(r, False) for r in rows(self.con, "SELECT * FROM ft_filings ORDER BY id DESC")]

    def ft_get(self, fid):
        out = self._ft_public(self._ft_row(fid))
        out["check"] = self.ft_check({"data": out["data"], "kind": out.get("kind") or "FT"})
        return out

    @staticmethod
    def _check_shape(data, kind="FT"):
        need(isinstance(data, dict), "filing data must be an object")
        if kind == "FZ":
            need(isinstance(data.get("actions", []), list), "actions must be a list")
            for a in data.get("actions", []):
                need(isinstance(a, dict), "each action must be an object")
                need(isinstance(a.get("containers", []), list) and isinstance(a.get("remarks", []), list), "containers and remarks must be lists")
            return
        need(isinstance(data.get("header", {}), dict), "header must be an object")
        need(isinstance(data.get("conveyances", []), list), "conveyances must be a list")
        for cv in data.get("conveyances", []):
            need(isinstance(cv, dict) and isinstance(cv.get("bills", []), list), "each conveyance needs a list of bills")
            for b in cv.get("bills", []):
                need(isinstance(b, dict) and isinstance(b.get("lines", []), list), "each bill needs a list of lines")
                for ln in b.get("lines", []):
                    need(isinstance(ln, dict), "each line must be an object")

    def ft_check(self, d):
        """Validate unsaved or saved data; returns the compile result with the usage-map counts."""
        data = d.get("data")
        kind = d.get("kind") or "FT"
        need(kind in KINDS, "kind must be FT or FZ")
        self._check_shape(data, kind)
        res = compile_any(kind, data)
        res["records"] = [{"id": r["id"], "path": r["path"], "text": r["text"]} for r in res["records"]]
        return res

    def ft_save(self, d):
        data = d.get("data")
        kind = d.get("kind") or (self._ft_row(d["id"])["kind"] if d.get("id") else "FT") or "FT"
        need(kind in KINDS, "kind must be FT or FZ")
        self._check_shape(data, kind)
        raw = json.dumps(data, separators=(",", ":"))
        need(len(raw) <= MAX_DATA_BYTES, "this filing is too large to save")
        res = compile_any(kind, data)
        label = str(d.get("label") or "").strip()[:80] or (res["admission_number"] or f"New {kind} filing")
        action = res.get("action") or None
        status = "ready" if res["ok"] else "draft"
        adm = d.get("admission_id") or None
        need(adm is None or one(self.con, "SELECT id FROM admissions WHERE id=?", (adm,)), "that admission does not exist")
        if d.get("id"):
            cur = self._ft_row(d["id"])
            self.con.execute("""UPDATE ft_filings SET label=?,admission_id=?,action=?,admission_number=?,status=?,data=?,updated_by=?,updated_at=? WHERE id=?""",
                             (label, adm if adm else cur["admission_id"], action, res["admission_number"], status, raw, self.user, now(), d["id"]))
            fid, what = d["id"], "update"
        else:
            fid = self.con.execute("""INSERT INTO ft_filings(kind,label,admission_id,action,admission_number,status,data,created_by,created_at,updated_by,updated_at)
                                      VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (kind, label, adm, action, res["admission_number"], status, raw, self.user, now(), self.user, now())).lastrowid
            what = "create"
        audit(self.con, self.user, what, "ft_filing", fid, {"kind": kind, "admission_number": res["admission_number"], "action": action, "ok": res["ok"]})
        return self.ft_get(fid)

    def ft_delete(self, fid):
        r = self._ft_row(fid)
        need(not one(self.con, "SELECT id FROM ft_exports WHERE filing_id=?", (fid,)),
             "this filing has been exported, so it is kept as a record; it cannot be deleted")
        self.con.execute("DELETE FROM ft_filings WHERE id=?", (fid,))
        audit(self.con, self.user, "delete", "ft_filing", fid, {"admission_number": r["admission_number"]})
        return {"ok": True}

    def ft_exports(self, fid):
        self._ft_row(fid)
        return rows(self.con, "SELECT id, ts, user, mode, eol, line_count, sha256 FROM ft_exports WHERE filing_id=? ORDER BY id DESC", (fid,))

    # ------------------------------------------------------------------ export
    def ft_export(self, fid, d):
        """Build the transmission file. The ABI password is used for this call only: it is not stored, logged or audited."""
        r = self._ft_row(fid)
        data = json.loads(r["data"])
        kind = r["kind"] or "FT"
        res = compile_any(kind, data)
        if not res["ok"]:
            raise ApiError(["Fix these before exporting:"] + res["errors"][:15] + ([f"…and {len(res['errors']) - 15} more"] if len(res["errors"]) > 15 else []))
        mode = d.get("mode") or "batch"
        need(mode in ("batch", "body"), "mode must be batch or body")
        eol = {"lf": "\n", "crlf": "\r\n"}.get((d.get("eol") or "lf").lower())
        need(eol, "line ending must be lf or crlf")
        body = [x["text"] for x in res["records"]]
        head = tail = None
        if mode == "batch":
            head, tail, errs = catair.build_envelope(self.abi_profile(), d.get("password"), app=kind)
            if errs:
                raise ApiError(errs)
        text = catair.render_file(body, head, tail, eol)
        digest = hashlib.sha256(catair.render_file(body, None, None, "\n").encode()).hexdigest()
        self.con.execute("INSERT INTO ft_exports(filing_id,ts,user,mode,eol,line_count,sha256,body) VALUES(?,?,?,?,?,?,?,?)",
                         (fid, now(), self.user, mode, "CRLF" if eol == "\r\n" else "LF", len(body), digest, "\n".join(body)))
        self.con.execute("UPDATE ft_filings SET status='exported', exported_at=? WHERE id=?", (now(), fid))
        audit(self.con, self.user, "export", "ft_filing", fid, {"admission_number": res["admission_number"], "mode": mode, "records": len(body), "sha256": digest})
        name = f"{kind}-{res['admission_number'] or fid}-{res['action']}.txt"
        return {"filename": re.sub(r"[^A-Za-z0-9._-]", "_", name), "text": text, "line_count": len(text.splitlines()), "sha256": digest,
                "warnings": res["warnings"]}

    # ------------------------------------------------------------------ prefill from an e214 admission
    def ft_from_admission(self, adm_id):
        a = self.get_admission(adm_id)
        zone = one(self.con, "SELECT * FROM zones WHERE id=?", (a["zone_id"],)) or {}
        notes = []
        todo = lambda m: notes.append(m)

        def party(pid):
            return one(self.con, "SELECT * FROM parties WHERE id=?", (pid,)) if pid else None

        def ior(p):
            m = IOR_ANY.search(str((p or {}).get("ident") or ""))
            return m.group(1) if m else ""
        op, imp, car = party(a["operator_id"]), party(a["importer_id"]), party(a["carrier_id"])
        digits = re.search(r"\d{3}", str(zone.get("zone_no") or ""))
        seq = re.search(r"(\d+)$", a["doc_no"])
        year = (a.get("entry_date") or "")[2:4] or ""
        h = {"action": "A", "zone_id": digits.group(0) if digits else "", "calendar_year": year, "control_number": seq.group(1) if seq else "",
             "port_code": zone.get("port_code") or "", "direct_delivery": "N", "abi_filer": self.abi_profile()["filer_code"],
             "zone_operator": ior(op), "firms": (zone.get("firms") or ""), "applicant": ior(imp) if ior(imp) != ior(op) else ""}
        if not zone.get("firms"):
            todo("FIRMS code of the zone site: add it to the zone in Setup, or type it into FT10")
        if not ior(op):
            todo("Zone Operator's Importer of Record number (NN-NNNNNNNNN): put it in the operator's ID under Setup → Parties, or type it into FT10")
        todo(f"Zone ID: this system holds '{zone.get('zone_no')}'. CATAIR needs the full 7- or 9-character Zone ID (FTZ number + sub-zone + site)")
        if not h["abi_filer"]:
            todo("ABI Filer Code: set it under Setup → Settings → ABI envelope, or type it into FT10")
        scac = str((car or {}).get("ident") or "").strip().upper()
        conv = {"admission_type": "A", "mot": "", "scac": scac if re.fullmatch(r"[A-Z]{2,4}", scac) else "",
                "conveyance_name": catair.clean_text((car or {}).get("name"), 23)[0], "voyage": catair.clean_text(a.get("vessel_voyage"), 15)[0],
                "export_date": "", "import_date": "", "port_unlading": "", "scheduled_arrival": ""}
        todo("Conveyance: Mode of Transportation (code from ACE CATAIR Appendix B), export and import dates and the port of unlading")
        bill = {"bill": catair.clean_text(a.get("transport_doc"), 35)[0].replace(" ", ""), "house_bill": "", "quantity": "", "country_export": "",
                "load_port": "", "inbond_numbers": [x for x in [re.sub(r"[^A-Za-z0-9]", "", str(a.get("inbond_ref") or "")).upper()] if x],
                "bonded_carriers": [], "containers": [], "lines": []}
        todo("Bill of lading: quantity (smallest exterior packaging unit), country of export and, for vessels, the foreign load port")
        lines_with_no_mid, rounded, uom_notes = [], [], False
        for i, l in enumerate(a["lines"], 1):
            p = one(self.con, "SELECT * FROM parts WHERE part_no=?", (l["part_no"],)) if l["part_no"] else None
            desc, changed = catair.clean_text(l["description"], 45)
            if changed:
                todo(f"Line {i}: the description was simplified to letters and numbers for CATAIR ('{desc}')")
            mid = (p or {}).get("mid") or ""
            if not mid:
                lines_with_no_mid.append(i)
            kg = str(l.get("uom2") or "").upper() == "KG" and l.get("qty2") is not None
            val = l["value"] or 0
            if abs(val - round(val)) > 1e-9:
                rounded.append(i)
            bill["lines"].append({
                "line_no": i, "htsus": re.sub(r"\D", "", str(l["htsus"] or "")), "coo": str(l["coo"] or "").upper(), "qty1": l["qty"], "uom1": str(l["uom"] or "").upper()[:3],
                "qty2": l["qty2"] if l.get("uom2") else "", "uom2": str(l.get("uom2") or "").upper()[:3],
                "weight": round(l["qty2"]) if kg else "", "value": round(val), "charges": "", "zone_status": catair.ZONE_STATUS.get(l["zone_status"], ""), "hmf": 0,
                "refs": [{"description": desc, "qualifier": "MID", "ref_id": mid}], "remarks": []})
            uom_notes = uom_notes or bool(l["uom"])
        if lines_with_no_mid:
            todo(f"Manufacturer ID (MID) is missing on line(s) {', '.join(map(str, lines_with_no_mid))}: add it to the product master or to the line")
        if rounded:
            todo(f"Value rounded to whole dollars on line(s) {', '.join(map(str, rounded))}")
        if uom_notes:
            todo("Unit of Measure was copied from the e214 line; CATAIR expects the statistical unit for the HTS number (for example NO or KG): check each line")
        todo("Weight and transportation charges: enter the gross weight and the freight to the zone in whole U.S. dollars (weight is filled only where a KG quantity existed)")
        conv["bills"] = [bill]
        data = {"header": h, "replace": {"reason_codes": [], "remarks": []}, "conveyances": [conv], "delete_remarks": []}
        saved = self.ft_save({"data": data, "label": f"{a['doc_no']} (from e214)", "admission_id": adm_id})
        saved["notes"] = notes
        audit(self.con, self.user, "prefill", "ft_filing", saved["id"], {"from_admission": a["doc_no"]})
        return saved

    # ------------------------------------------------------------------ CBP responses (NF) and broker-download copies (ZD)
    def ft_decode(self, d):
        text = str((d or {}).get("text") or "")
        need(text.strip(), "paste the CBP response text first")
        need(len(text) <= MAX_RESPONSE, "that file is too large")
        return catair_fz.parse_output(text, str(d.get("kind") or "auto").lower())

    def ft_matches(self, result):
        """Filings a response is about: same admission number, or (FZ) the bill / in-bond / container it names."""
        keys = {x for x in [result.get("admission_number")] + [r.get("id") for r in result.get("refs", [])] if x}
        return [f for f in rows(self.con, "SELECT id, kind, label, admission_number, status FROM ft_filings WHERE admission_number<>''")
                if f["admission_number"] in keys]

    def ft_response(self, d):
        """Decode a pasted CBP response, log it, and move the matching filings to accepted / rejected / authorized."""
        dec = self.ft_decode(d)
        need(dec["kind"] == "nf", "this looks like a ZD copy, not a CBP response; use Decode to read it")
        need(dec["results"], "; ".join(dec["problems"]) or "no response found")
        pinned = d.get("filing_id")
        pin_row = self._ft_row(pinned) if pinned else None
        out = []
        raw = "\n".join(x["text"].rstrip() for x in dec["records"])
        for r in dec["results"]:
            targets = [pin_row] if pin_row else self.ft_matches(r)
            msg = r["messages"][0] if r["messages"] else {}
            for t in targets:
                self.con.execute("""INSERT INTO ft_responses(filing_id,ts,user,admission_number,disposition,message_type,message_code,outcome,summary,raw)
                                    VALUES(?,?,?,?,?,?,?,?,?,?)""", (t["id"], now(), self.user, r["admission_number"], r["disposition"], msg.get("type"),
                                                                    msg.get("code"), r["outcome"], r["summary"][:300], raw))
                new = {"accepted": "accepted", "warning": "accepted", "rejected": "rejected", "authorized": "authorized"}.get(r["outcome"])
                cur = one(self.con, "SELECT status FROM ft_filings WHERE id=?", (t["id"],))["status"]
                if new and (new == "rejected" or STATUS_RANK.get(new, 0) >= STATUS_RANK.get(cur, 0)):
                    self.con.execute("UPDATE ft_filings SET status=? WHERE id=?", (new, t["id"]))
                audit(self.con, self.user, "response", "ft_filing", t["id"], {"outcome": r["outcome"], "disposition": r["disposition"], "code": msg.get("code")})
            r["filings"] = [{"id": t["id"], "label": t["label"], "kind": t["kind"]} for t in targets]
            out.append(r)
        return {"results": out, "records": dec["records"], "problems": dec["problems"], "matched": sum(1 for r in out if r["filings"])}

    def ft_responses(self, fid):
        self._ft_row(fid)
        return rows(self.con, "SELECT id, ts, user, admission_number, disposition, message_code, outcome, summary FROM ft_responses WHERE filing_id=? ORDER BY id DESC", (fid,))
