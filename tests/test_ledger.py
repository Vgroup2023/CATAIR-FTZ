"""FIFO, dual units, tamper-evidence, hard stops, product master, WMS validation, reconciliation, integrations."""
import json
import os
import sqlite3
import unittest

os.environ.setdefault("FTZ_PBKDF2_ITER", "1000")

from ftz import db, recon
from ftz.service import ApiError, Service
from tests.test_ftz import TODAY, Base, Site, days, line


def wd(s, lot, q, **kw):
    return s.withdraw({"lot_id": lot["id"], "kind": "export", "qty": q, "date": TODAY, "export_ref": "X", **kw})


class TestFifo(Base):
    def two_lots(self, **kw):
        self.admission([line(part_no="SPK", **kw)], entry_date=days(-10), bl="OLD")
        self.admission([line(part_no="SPK", **kw)], entry_date=days(-1), bl="NEW")
        old, new = sorted(self.s.list_lots(), key=lambda l: l["received_on"])
        return old, new

    def test_newer_lot_blocked_until_older_is_used(self):
        old, new = self.two_lots()
        with self.assertRaises(ApiError) as c:
            wd(self.s, new, 10)
        self.assertIn("FIFO", str(c.exception))
        wd(self.s, old, 100)                      # oldest first is fine
        wd(self.s, new, 10)                       # and the newer lot is now allowed

    def test_partly_used_older_lot_still_blocks(self):
        old, new = self.two_lots()
        wd(self.s, old, 40)
        with self.assertRaises(ApiError):
            wd(self.s, new, 1)

    def test_fifo_withdraw_splits_across_lots_oldest_first(self):
        old, new = self.two_lots()
        key = self.s.stock_summary()[0]
        self.assertEqual((key["on_hand"], key["lots"]), (200, 2))
        r = self.s.withdraw_fifo({"key": key["key"], "zone_id": self.zone, "zone_status": "PF", "kind": "consumption",
                                  "qty": 130, "date": TODAY, "entry_no": "E77"})
        self.assertEqual([(a["lot_no"], a["qty"]) for a in r["allocations"]], [(old["lot_no"], 100), (new["lot_no"], 30)])
        self.assertEqual(r["estimated_duty"], 65.0)               # 130 x $10 x 5%
        left = {l["id"]: l["qty_on_hand"] for l in self.s.list_lots()}
        self.assertEqual((left[old["id"]], left[new["id"]]), (0, 70))
        with self.assertRaises(ApiError):
            self.s.withdraw_fifo({"key": key["key"], "zone_id": self.zone, "zone_status": "PF", "kind": "export",
                                  "qty": 71, "date": TODAY, "export_ref": "X"})

    def test_specific_identification_is_an_explicit_alternative(self):
        old, new = self.two_lots()
        self.s.set_settings({"inventory_method": "specific"})
        wd(self.s, new, 10)                                        # allowed only when the method is switched
        with self.assertRaises(ApiError):
            self.s.set_settings({"inventory_method": "lifo"})

    def test_different_status_or_part_is_not_the_same_item(self):
        self.admission([line(part_no="SPK", zone_status="NPF", duty_rate=None)], entry_date=days(-10), bl="A")
        self.admission([line(part_no="SPK")], entry_date=days(-1), bl="B")
        pf = next(l for l in self.s.list_lots() if l["zone_status"] == "PF")
        wd(self.s, pf, 5)

    def test_inbond_and_manufacture_follow_fifo(self):
        old, new = self.two_lots()
        with self.assertRaises(ApiError):
            self.s.create_inbond(dict(type="TE", zone_id=self.zone, carrier_id=self.car, mode="truck", origin_port="1001",
                                      dest_port="2002", bl_no="B", issued_date=TODAY, lines=[{"lot_id": new["id"], "qty": 5}]))
        b = self.s.create_inbond(dict(type="TE", zone_id=self.zone, carrier_id=self.car, mode="truck", origin_port="1001",
                                      dest_port="2002", bl_no="B", issued_date=TODAY,
                                      lines=[{"lot_id": old["id"], "qty": 100}, {"lot_id": new["id"], "qty": 5}]))
        self.assertEqual(len(b["lines"]), 2)                       # taking the older lot in full in the same document is fine


class TestDualUnits(Base):
    def kg_lot(self):
        self.admission([line(qty=30, value=300, qty2=10, uom2="KG", part_no="TEE")])
        return self.s.list_lots()[0]

    def test_no_rounding_residue_across_partial_withdrawals(self):
        lot = self.kg_lot()
        for _ in range(3):
            wd(self.s, lot, 10)
        end = self.s.list_lots()[0]
        self.assertEqual((end["qty_on_hand"], end["qty2_on_hand"]), (0, 0))       # the last piece takes the remainder
        moves = self.s.lot_movements(lot["id"])
        self.assertEqual(round(sum(m["qty2"] for m in moves), 4), 0)               # 10 KG in, exactly 10 KG out
        self.assertEqual(sorted(round(-m["qty2"], 4) for m in moves if m["qty2"] < 0), [3.3333, 3.3333, 3.3334])

    def test_cancel_and_returns_restore_customs_quantity_exactly(self):
        lot = self.kg_lot()
        b = self.s.create_inbond(dict(type="TE", zone_id=self.zone, carrier_id=self.car, mode="truck", origin_port="1001",
                                      dest_port="2002", bl_no="B", issued_date=TODAY, lines=[{"lot_id": lot["id"], "qty": 7}]))
        self.assertEqual(b["lines"][0]["qty2"], 2.3333)
        self.s.inbond_action(b["id"], "cancel")
        back = self.s.list_lots()[0]
        self.assertEqual((back["qty_on_hand"], back["qty2_on_hand"]), (30, 10))
        p = self.permit()
        a = self.s.perform_activity({"permit_id": p["id"], "lot_id": lot["id"], "kind": "temp_removal", "qty": 20,
                                     "performed_on": TODAY, "expected_return": days(5)})
        mid = self.s.list_lots()[0]
        self.assertEqual((mid["qty_out"], mid["qty2_out"]), (20, 6.6667))
        self.s.return_removal(a["id"], {"qty": 5, "date": TODAY})
        self.s.return_removal(a["id"], {"qty": 15, "date": TODAY})
        end = self.s.list_lots()[0]
        self.assertEqual((end["qty_on_hand"], end["qty2_on_hand"], end["qty_out"], end["qty2_out"]), (30, 10, 0, 0))
        self.assertEqual(self.s.reconcile()["ledger_breaks"], [])

    def test_validation_and_adjustments(self):
        with self.assertRaises(ApiError):
            self.admission([line(qty2=5)])                         # customs quantity without a unit
        lot = self.kg_lot()
        self.s.adjust({"lot_id": lot["id"], "delta": -6, "reason": "count"})
        adj = self.s.list_lots()[0]
        self.assertEqual((adj["qty_on_hand"], adj["qty2_on_hand"]), (24, 8))
        self.assertEqual(self.s.reconcile()["ledger_breaks"], [])

    def test_transfer_keeps_both_units(self):
        lot = self.kg_lot()
        z2 = self.s.create_zone({"zone_no": "FTZ 2", "name": "Two"})["id"]
        self.s.withdraw({"lot_id": lot["id"], "kind": "transfer", "qty": 15, "date": TODAY, "dest_zone_id": z2, "export_ref": "T1"})
        dest = next(l for l in self.s.list_lots() if l["zone_id"] == z2)
        self.assertEqual((dest["qty_on_hand"], dest["qty2_on_hand"], dest["uom2"], dest["received_on"]), (15, 5, "KG", TODAY))


class TestHardStops(Base):
    def test_cannot_ship_before_admission_date(self):
        lot = self.lot(entry_date=days(-2))
        with self.assertRaises(ApiError) as c:
            self.s.withdraw({"lot_id": lot["id"], "kind": "export", "qty": 1, "date": days(-5), "export_ref": "X"})
        self.assertIn("before lot", str(c.exception))
        p = self.permit()
        with self.assertRaises(ApiError):
            self.s.perform_activity({"permit_id": p["id"], "lot_id": lot["id"], "kind": "destroy", "qty": 1,
                                     "performed_on": days(-5), "note": "n"})

    def test_database_refuses_negative_stock(self):
        lot = self.lot()
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute("UPDATE lots SET qty_on_hand=-1 WHERE id=?", (lot["id"],))

    def test_history_is_append_only(self):
        lot = self.lot()
        for sql in ("UPDATE audit SET user='x'", "DELETE FROM audit", "UPDATE movements SET qty=999", "DELETE FROM movements"):
            with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                self.con.execute(sql)

    def test_tampering_is_detected_even_if_safeguards_are_dropped(self):
        lot = self.lot()
        wd(self.s, lot, 10)
        self.assertTrue(self.s.verify_integrity()["ok"])
        self.con.execute("DROP TRIGGER movements_no_update")
        self.con.execute("UPDATE movements SET qty=-1 WHERE kind='withdraw_export'")
        res = self.s.verify_integrity()
        self.assertFalse(res["ok"])
        self.assertIn("changed after it was recorded", res["tables"]["movements"]["breaks"][0])
        self.con.execute("DROP TRIGGER audit_no_delete")
        self.con.execute("DELETE FROM audit WHERE id=(SELECT MIN(id) FROM audit)")
        self.assertTrue(self.s.verify_integrity()["tables"]["audit"]["breaks"])

    def test_reconciliation_catches_a_balance_edited_behind_the_ledgers_back(self):
        lot = self.lot()
        self.con.execute("UPDATE lots SET qty_on_hand=qty_on_hand+5 WHERE id=?", (lot["id"],))
        breaks = self.s.reconcile()["ledger_breaks"]
        self.assertTrue(any(lot["lot_no"] in b for b in breaks))


class TestProductMaster(Base):
    def part(self, **kw):
        base = dict(part_no="SPK", description="Speakers", htsus="8518220000", coo="cn", uom="PCS", uom2="KG", conv=0.5,
                    duty_rate=4.9, default_status="PF", pga_agencies="fcc, cpsc")
        base.update(kw)
        return self.s.upsert_part(base)

    def test_master_fills_blank_line_fields_and_computes_customs_qty(self):
        self.part()
        a = self.s.save_admission({"zone_id": self.zone, "lines": [{"part_no": "spk", "qty": 200, "value": 1000}]})
        ln = a["lines"][0]
        self.assertEqual((ln["description"], ln["htsus"], ln["coo"], ln["uom"], ln["zone_status"], ln["duty_rate"]),
                         ("Speakers", "8518.22.0000", "CN", "PCS", "PF", 4.9))
        self.assertEqual((ln["qty2"], ln["uom2"]), (100.0, "KG"))
        keep = self.s.save_admission({"zone_id": self.zone, "lines": [{"part_no": "SPK", "qty": 1, "value": 1, "description": "Mine", "coo": "VN"}]})
        self.assertEqual((keep["lines"][0]["description"], keep["lines"][0]["coo"]), ("Mine", "VN"))   # explicit entries win

    def test_validation_and_row_level_import_errors(self):
        for bad in (dict(htsus="12"), dict(default_status="X"), dict(conv=-1), dict(pga_agencies="!!"), dict(part_no="")):
            with self.assertRaises(ApiError, msg=bad):
                self.part(**bad)
        res = self.s.import_parts({"rows": [{"part_no": "A1", "htsus": "8518.22.0000"}, {"part_no": "B2", "htsus": "bad"}, {"PART_NO": "C3"}]})
        self.assertEqual(res["imported"], 2)
        self.assertEqual([e["row"] for e in res["errors"]], [2])

    def test_check_warns_on_master_pga_and_unknown_parts(self):
        self.part()
        a = self.s.save_admission({"zone_id": self.zone, "operator_id": self.op, "carrier_id": self.car, "transport_doc": "BL5",
                                   "port_of_entry": "1001", "entry_date": TODAY,
                                   "lines": [dict(line(part_no="SPK", htsus="9999.99.9999")), dict(line(part_no="NOPE"))]})
        w = " | ".join(self.s.admission_check(a["id"])["warnings"])
        self.assertIn("differs from the product master", w)
        self.assertIn("FCC,CPSC", w)
        self.assertIn("not in the product master", w)
        ok = self.s.save_admission({"zone_id": self.zone, "operator_id": self.op, "carrier_id": self.car, "transport_doc": "BL5",
                                    "port_of_entry": "1001", "entry_date": TODAY, "lines": [dict(line(part_no="SPK", htsus="8518.22.0000", pga_ref="FCC-DISCLAIMER-A", qty2=100, uom2="KG"))]})
        self.assertNotIn("FCC", " ".join(self.s.admission_check(ok["id"])["warnings"]))


class TestWms(Base):
    def draft(self, qty=100, bl="BL9", part="SPK"):
        return self.s.save_admission({"zone_id": self.zone, "operator_id": self.op, "carrier_id": self.car, "transport_doc": bl,
                                      "port_of_entry": "1001", "entry_date": TODAY, "lines": [line(part_no=part, qty=qty)]})

    def receipts(self, *rows):
        return self.s.import_receipts({"rows": [dict(receipt_no=f"R{i}", ref="BL9", uom="PCS", received_on=TODAY, **r) for i, r in enumerate(rows, 1)]})

    def test_no_wms_data_means_nothing_to_compare(self):
        self.assertEqual(self.s.admission_check(self.draft()["id"])["wms_findings"], 0)

    def test_matching_receipt_is_clean(self):
        self.receipts(dict(part_no="SPK", qty=60), dict(part_no="SPK", qty=40))       # receipts add up per part
        self.assertEqual(self.s.admission_check(self.draft()["id"])["wms_findings"], 0)

    def test_mismatch_missing_and_extra_are_reported(self):
        self.receipts(dict(part_no="SPK", qty=90), dict(part_no="OTHER", qty=5))
        chk = self.s.admission_check(self.draft()["id"])
        text = " | ".join(chk["warnings"])
        self.assertIn("WMS received 90.0 of SPK but the e214 line says 100", text)
        self.assertIn("OTHER on this B/L that is not on the e214", text)
        self.assertIn("No WMS receiving log", " | ".join(self.s.admission_check(self.draft(bl="BL-NONE")["id"])["warnings"]))

    def test_strict_mode_blocks_submission_before_it_happens(self):
        self.receipts(dict(part_no="SPK", qty=90))
        d = self.draft()
        self.s.admission_action(d["id"], "submit")                                     # warn-only by default
        self.s.set_settings({"require_wms_match": "1"})
        d2 = self.draft()
        with self.assertRaises(ApiError) as c:
            self.s.admission_action(d2["id"], "submit")
        self.assertIn("WMS mismatch", str(c.exception))
        self.receipts(dict(part_no="SPK", qty=100))
        self.s.admission_action(d2["id"], "submit")

    def test_import_row_errors_and_upsert(self):
        res = self.s.import_receipts({"rows": [{"receipt_no": "R1", "part_no": "A", "qty": 5, "ref": "X"}, {"receipt_no": "R2", "part_no": "A", "qty": 0}]})
        self.assertEqual((res["imported"], len(res["errors"])), (1, 1))
        self.s.import_receipts({"rows": [{"receipt_no": "R1", "part_no": "A", "qty": 7, "ref": "X"}]})   # same key updates
        self.assertEqual([r["qty"] for r in self.s.list_wms()["receipts"]], [7])

    def test_reconciliation_flags_overage_shortage_and_unmatched_receipts(self):
        self.admission([line(part_no="SPK", qty=100)], bl="BL1")
        self.admission([line(part_no="CBL", qty=50)], bl="BL2")
        self.s.import_wms_inventory({"snapshot_on": TODAY, "rows": [{"part_no": "SPK", "qty": 95, "uom": "PCS"}, {"part_no": "CBL", "qty": 55},
                                                                       {"part_no": "GHOST", "qty": 3}]})
        self.s.import_receipts({"rows": [{"receipt_no": "R9", "part_no": "ZZZ", "qty": 5, "ref": "UNKNOWN-BL"}]})
        r = self.s.reconcile()
        kinds = {v["part_no"]: v["kind"].split()[0] for v in r["wms_variances"]}
        self.assertEqual(kinds, {"SPK": "shortage", "CBL": "overage", "GHOST": "overage"})
        self.assertEqual([u["ref"] for u in r["unmatched_receipts"]], ["UNKNOWN-BL"])
        self.assertEqual(len(r["admission_mismatches"]), 2)          # approved e214s with no WMS receiving log while WMS data exists
        self.assertEqual(r["issues"], 6)


class TestReconRuns(Base):
    def test_runs_are_stored_and_the_daily_job_runs_once(self):
        self.lot()
        first = self.s.run_reconciliation()
        ov = self.s.recon_overview()
        self.assertEqual((ov["latest"]["id"], ov["latest"]["issues"], len(ov["runs"])), (first["id"], 0, 1))
        self.assertIsNone(recon.maybe_run_daily(self.con, hours=24))                 # already ran today
        self.con.execute("UPDATE recon_runs SET run_at='2000-01-01T00:00:00'")
        self.assertEqual(recon.maybe_run_daily(self.con, hours=24)["kind"], "daily")
        self.assertEqual(self.s.dashboard()["recon"]["kind"], "daily")

    def test_overdue_items_count_as_issues(self):
        lot = self.lot(entry_date=days(-60))
        self.s.create_inbond(dict(type="TE", zone_id=self.zone, carrier_id=self.car, mode="truck", origin_port="1001",
                                  dest_port="2002", bl_no="B", issued_date=days(-45), lines=[{"lot_id": lot["id"], "qty": 5}]))
        self.assertEqual(len(self.s.reconcile()["overdue_inbonds"]), 1)


class TestEntryWorksheet(Base):
    def test_worksheet_comes_from_the_ledger(self):
        lot = self.lot(part_no="SPK")
        self.s.withdraw({"lot_id": lot["id"], "kind": "consumption", "qty": 40, "date": TODAY, "entry_no": "E-1"})
        self.s.withdraw({"lot_id": lot["id"], "kind": "consumption", "qty": 10, "date": TODAY, "entry_no": "E-1"})
        w = self.s.entry_worksheet("E-1")
        self.assertEqual((len(w["lines"]), w["total_value"], w["total_duty"]), (2, 500.0, 25.0))
        self.assertEqual((w["lines"][0]["htsus"], w["lines"][0]["admission_no"].startswith("e214-")), ("8471.30.0100", True))
        with self.assertRaises(ApiError):
            self.s.entry_worksheet("NOPE")


class TestIntegrationHttp(unittest.TestCase):
    def setUp(self):
        from ftz import auth
        auth.LIMITS.h.clear()
        self.site = Site(FTZ_AUTH_USER="owner", FTZ_AUTH_PASSWORD="owner-pass-12345", FTZ_DEMO_MODE="1")
        self.site.login("owner", "owner-pass-12345")

    def tearDown(self):
        self.site.close()

    def anon(self, method, path, body=None, token=None):
        s = Site.__new__(Site)
        s.__dict__.update(self.site.__dict__)
        s.cookie = None
        return s.call(method, path, body, headers={"Authorization": f"Bearer {token}"} if token else None)

    def test_token_lifecycle_and_scope(self):
        st, body, _ = self.site.call("POST", "/api/tokens", {"name": "WMS feed"})
        self.assertEqual(st, 200)
        tok = json.loads(body)
        self.assertTrue(tok["token"].startswith("ftz_"))
        self.assertNotIn(tok["token"].encode(), self.site.call("GET", "/api/tokens")[1])     # secret shown once, never listed
        rows = {"rows": [{"receipt_no": "R1", "part_no": "SPK", "qty": 5, "ref": "BL1"}]}
        self.assertEqual(self.anon("POST", "/api/wms/receipts", rows, tok["token"])[0], 200)
        self.assertEqual(self.anon("POST", "/api/parts/import", {"rows": [{"part_no": "SPK"}]}, tok["token"])[0], 200)
        self.assertEqual(self.anon("POST", "/api/zones", {"zone_no": "Z", "name": "N"}, tok["token"])[0], 403)   # out of scope
        self.assertEqual(self.anon("GET", "/api/audit", None, tok["token"])[0], 403)
        self.assertEqual(self.anon("GET", "/backup", None, tok["token"])[0], 403)
        self.assertEqual(self.anon("POST", "/api/wms/receipts", rows, "ftz_wrong")[0], 401)
        self.assertEqual(self.anon("POST", "/api/wms/receipts", rows)[0], 302 if False else 401)
        audit = json.loads(self.site.call("GET", "/api/audit")[1])
        self.assertTrue(any(a["user"] == "api:WMS feed" for a in audit))
        self.assertEqual(self.site.call("POST", f"/api/tokens/{tok['id']}/revoke", {})[0], 200)
        self.assertEqual(self.anon("POST", "/api/wms/receipts", rows, tok["token"])[0], 401)

    def test_demo_user_cannot_manage_tokens_or_verify_secrets(self):
        cfg = json.loads(self.site.call("GET", "/auth/config")[1])["demo"]
        d = Site.__new__(Site)
        d.__dict__.update(self.site.__dict__)
        d.cookie = None
        self.assertEqual(d.login(cfg["username"], cfg["password"])[0], 200)
        self.assertEqual(d.call("POST", "/api/tokens", {"name": "evil"})[0], 403)
        self.assertEqual(d.call("GET", "/api/tokens")[0], 403)

    def test_entry_worksheet_page_and_integrity_endpoint(self):
        con = db.connect(os.environ["FTZ_DB"])
        s = Service(con, "seed")
        lot = min((l for l in s.list_lots() if l["zone_status"] == "PF" and l["qty_on_hand"] > 0), key=lambda l: (l["received_on"], l["id"]))   # FIFO: oldest first
        s.withdraw({"lot_id": lot["id"], "kind": "consumption", "qty": 5, "date": TODAY, "entry_no": "ENT 42/A"})
        con.commit()
        con.close()
        st, body, _ = self.site.call("GET", "/print/entry/ENT%2042%2FA")
        self.assertEqual(st, 200)
        self.assertIn(b"3461 / 7501", body)
        self.assertEqual(self.site.call("GET", "/print/entry/NOPE")[0], 404)
        self.assertTrue(json.loads(self.site.call("GET", "/api/integrity")[1])["ok"])
        self.assertEqual(self.site.call("POST", "/api/recon/run", {})[0], 200)
        self.assertEqual(self.site.call("GET", "/api/admissions/1/check")[0], 200)

    def test_safeguard_error_is_reported_cleanly(self):
        con = db.connect(os.environ["FTZ_DB"])
        lot = Service(con).list_lots()[0]
        con.close()
        st, body, _ = self.site.call("POST", "/api/adjustments", {"lot_id": lot["id"], "delta": -10 ** 6, "reason": "test"})
        self.assertEqual(st, 400)


if __name__ == "__main__":
    unittest.main()
