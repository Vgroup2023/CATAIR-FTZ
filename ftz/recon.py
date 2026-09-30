"""Reconciliation, tamper detection and the daily look-back. Mixed into Service."""
import json
import sys
from datetime import datetime, timedelta

from . import rules
from .common import r4
from .db import AUDIT_FIELDS, MOVE_FIELDS, audit, chain_hash, now, one, rows

EPS = 1e-4


class ReconMixin:
    # -------------------------------------------------------------- tamper check
    def verify_integrity(self):
        """Recompute the hash chain over the audit log and the stock ledger."""
        result = {"ok": True, "tables": {}}
        for table, fields in (("audit", AUDIT_FIELDS), ("movements", MOVE_FIELDS)):
            prev, n, breaks = "", 0, []
            for r in self.con.execute(f"SELECT * FROM {table} WHERE hash IS NOT NULL ORDER BY id"):
                r = dict(r)
                if r["prev_hash"] != prev:
                    breaks.append(f"{table} row {r['id']}: an earlier row was removed or replaced")
                elif r["hash"] != chain_hash(prev, *[r[f] for f in fields]):
                    breaks.append(f"{table} row {r['id']}: content was changed after it was recorded")
                prev, n = r["hash"], n + 1
            legacy = self.con.execute(f"SELECT COUNT(*) FROM {table} WHERE hash IS NULL").fetchone()[0]
            result["tables"][table] = {"rows_verified": n, "rows_before_chaining": legacy, "breaks": breaks[:50], "head": prev}
            result["ok"] = result["ok"] and not breaks
        return result

    # ------------------------------------------------------------- reconciliation
    def reconcile(self):
        con = self.con
        ledger = []
        for r in rows(con, """SELECT l.id, l.lot_no, l.qty_on_hand, l.qty_out, l.qty2_on_hand, l.qty2_out, l.uom, l.uom2,
                COALESCE(SUM(m.qty),0) s, COALESCE(SUM(m.qty2),0) s2,
                COALESCE(SUM(CASE WHEN m.kind IN ('temp_out','temp_in') THEN -m.qty ELSE 0 END),0) o,
                COALESCE(SUM(CASE WHEN m.kind IN ('temp_out','temp_in') THEN -COALESCE(m.qty2,0) ELSE 0 END),0) o2
                FROM lots l LEFT JOIN movements m ON m.lot_id=l.id GROUP BY l.id"""):
            if abs(r["qty_on_hand"] - r["s"]) > EPS:
                ledger.append(f"Lot {r['lot_no']}: balance is {r4(r['qty_on_hand'])} {r['uom']} but the movement ledger adds up to {r4(r['s'])}")
            if abs(r["qty_out"] - r["o"]) > EPS:
                ledger.append(f"Lot {r['lot_no']}: {r4(r['qty_out'])} shown as temporarily removed but the ledger says {r4(r['o'])}")
            if r["qty2_on_hand"] is not None and abs(r["qty2_on_hand"] - r["s2"]) > EPS:
                ledger.append(f"Lot {r['lot_no']}: customs-unit balance {r4(r['qty2_on_hand'])} {r['uom2']} but the ledger adds up to {r4(r['s2'])}")
        # Physical (WMS) versus customs ledger, from the latest WMS inventory snapshot.
        snap = con.execute("SELECT MAX(snapshot_on) FROM wms_inventory").fetchone()[0]
        variances = []
        if snap:
            wms = {r["part_no"].lower(): r for r in rows(con, "SELECT * FROM wms_inventory WHERE snapshot_on=?", (snap,))}
            led = {r["pn"]: r for r in rows(con, """SELECT LOWER(part_no) pn, MIN(part_no) part_no, MIN(uom) uom, ROUND(SUM(qty_on_hand),4) q
                                                    FROM lots WHERE part_no IS NOT NULL GROUP BY LOWER(part_no)""")}
            for pn in sorted(set(wms) | set(led)):
                w, l = wms.get(pn), led.get(pn)
                wq, lq = (w["qty"] if w else 0.0), (l["q"] if l else 0.0)
                if abs(wq - lq) > EPS:
                    variances.append({"part_no": (w or l)["part_no"], "wms": r4(wq), "ledger": r4(lq), "diff": r4(wq - lq),
                                      "kind": "overage (WMS has more than the customs ledger)" if wq > lq else "shortage (WMS has less than the customs ledger)",
                                      "uom": (l or w)["uom"]})
        unmatched = rows(con, """SELECT ref, COUNT(*) n, GROUP_CONCAT(DISTINCT part_no) parts FROM wms_receipts
                                 WHERE ref IS NOT NULL AND LOWER(ref) NOT IN (SELECT LOWER(transport_doc) FROM admissions
                                 WHERE transport_doc IS NOT NULL AND status<>'rejected') GROUP BY ref""")
        mismatches = []
        for a in rows(con, "SELECT id, doc_no FROM admissions WHERE status IN ('submitted','approved')"):
            for f in self._wms_findings(self.get_admission(a["id"])):
                mismatches.append(f"{a['doc_no']}: {f}")
        ov_ib = [b["doc_no"] for b in self.list_inbonds() if b["overdue"]]
        ov_rm = [a["act_no"] for a in self.list_activities() if a["overdue"]]
        summary = {"lots_checked": con.execute("SELECT COUNT(*) FROM lots").fetchone()[0], "ledger_breaks": ledger,
                   "wms_snapshot_on": snap, "wms_variances": variances, "unmatched_receipts": unmatched,
                   "admission_mismatches": mismatches, "overdue_inbonds": ov_ib, "overdue_removals": ov_rm}
        summary["issues"] = (len(ledger) + len(variances) + len(unmatched) + len(mismatches) + len(ov_ib) + len(ov_rm))
        return summary

    def run_reconciliation(self, kind="manual"):
        summary = self.reconcile()
        cur = self.con.execute("INSERT INTO recon_runs(run_at,kind,run_by,issues,summary) VALUES(?,?,?,?,?)",
                               (now(), kind, self.user, summary["issues"], json.dumps(summary, default=str)))
        audit(self.con, self.user, "reconcile", "recon_run", cur.lastrowid, {"kind": kind, "issues": summary["issues"]})
        return {"id": cur.lastrowid, "run_at": now(), "kind": kind, **summary}

    def recon_overview(self):
        runs = rows(self.con, "SELECT id, run_at, kind, run_by, issues FROM recon_runs ORDER BY id DESC LIMIT 20")
        last = one(self.con, "SELECT * FROM recon_runs ORDER BY id DESC LIMIT 1")
        latest = None
        if last:
            latest = {"id": last["id"], "run_at": last["run_at"], "kind": last["kind"], **json.loads(last["summary"])}
        return {"latest": latest, "runs": runs}


def maybe_run_daily(con, hours=24):
    """Run the reconciliation if the last run is older than `hours`. Returns the summary or None."""
    from .service import Service
    last = con.execute("SELECT run_at FROM recon_runs ORDER BY id DESC LIMIT 1").fetchone()
    if last and datetime.fromisoformat(last[0]) > datetime.now() - timedelta(hours=hours):
        return None
    if not con.in_transaction:
        con.execute("BEGIN IMMEDIATE")
    try:
        res = Service(con, "system").run_reconciliation("daily")
        con.commit()
        return res
    except BaseException:
        con.rollback()
        raise


def scheduler_loop(stop, hours=24, every=900):
    """Background thread: check every 15 minutes whether the daily reconciliation is due."""
    from . import db
    while not stop.wait(every):
        try:
            con = db.connect()
            try:
                maybe_run_daily(con, hours)
            finally:
                con.close()
        except Exception as ex:
            sys.stderr.write(f"scheduled reconciliation failed: {ex!r}\n")
