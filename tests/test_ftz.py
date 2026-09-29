import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, timedelta

from ftz import db, rules
from ftz.service import ApiError, Service

TODAY = date.today().isoformat()


def days(n):
    return (date.today() + timedelta(days=n)).isoformat()


def line(**kw):
    base = dict(description="Widgets", htsus="8471.30.0100", coo="CN", qty=100, uom="PCS", value=1000,
                zone_status="PF", duty_rate=5, location="A-1")
    base.update(kw)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        db.init(self.con)
        self.s = Service(self.con, "tester")
        self.zone = self.s.create_zone({"zone_no": "FTZ 999", "name": "Test Zone", "port_code": "1001"})["id"]

        def mk(kind, name):
            return self.s.create_party({"kind": kind, "name": name})["id"]
        self.op, self.car, self.imp = mk("operator", "Op Co"), mk("carrier", "Carrier Co"), mk("importer", "Imp Co")

    def admission(self, lines=None, approve=True):
        a = self.s.save_admission({
            "zone_id": self.zone, "operator_id": self.op, "carrier_id": self.car, "importer_id": self.imp,
            "transport_mode": "truck", "transport_doc": "BL1", "port_of_entry": "1001", "entry_date": TODAY,
            "lines": lines or [line()]})
        self.s.admission_action(a["id"], "submit")
        if approve:
            a = self.s.admission_action(a["id"], "approve", {"cbp_ref": "ACE-1"})
        return a

    def lot(self, **kw):
        self.admission([line(**kw)])
        return self.s.list_lots()[0]

    def permit(self, acts=("manipulate", "manufacture", "exhibit", "destroy", "temp_removal"), **kw):
        p = self.s.save_permit({"zone_id": self.zone, "operator_id": self.op, "kind": "blanket",
                                "activities": list(acts), "valid_from": days(-10), "valid_to": days(300), **kw})
        return self.s.permit_action(p["id"], "activate", {"cbp_ref": "P-1"})


class TestRules(unittest.TestCase):
    def test_hts_normalised_and_checked(self):
        self.assertEqual(rules.normalize_hts("8471300100"), "8471.30.0100")
        self.assertTrue(rules.validate_line(line(htsus="1234"), 1))

    def test_pf_needs_rate_and_foreign_needs_coo(self):
        self.assertTrue(any("duty rate" in e for e in rules.validate_line(line(duty_rate=None), 1)))
        self.assertTrue(any("origin" in e for e in rules.validate_line(line(coo=""), 1)))
        self.assertFalse(rules.validate_line(line(zone_status="D", coo="", htsus="", duty_rate=None), 1))

    def test_sheet_layout(self):
        s = rules.sheet_layout(list(range(25)), 10, False)
        self.assertEqual([x["form"] for x in s], ["214", "214B", "214B"])
        self.assertEqual([len(x["lines"]) for x in s], [10, 10, 5])
        self.assertEqual([x["form"] for x in rules.sheet_layout(list(range(11)), 10, True)], ["214A", "214C"])

    def test_inbond_port_rules(self):
        h = dict(type="IE", carrier_id=1, origin_port="1001", dest_port="2002", issued_date=TODAY, bl_no="x", mode="truck")
        self.assertTrue(any("must match" in e for e in rules.validate_inbond(h, [line()])))
        h.update(type="TE", dest_port="1001")
        self.assertTrue(any("differ" in e for e in rules.validate_inbond(h, [line()])))
        h.update(type="IT", dest_port="2002")
        self.assertTrue(any("zone-restricted" in e for e in rules.validate_inbond(h, [line(zone_status="ZR")])))


class TestAdmissions(Base):
    def test_lifecycle_creates_lots(self):
        a = self.admission()
        self.assertEqual(a["status"], "approved")
        lots = self.s.list_lots()
        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0]["qty_on_hand"], 100)
        self.assertEqual(lots[0]["unit_value"], 10)

    def test_submit_validates(self):
        a = self.s.save_admission({"lines": []})
        with self.assertRaises(ApiError) as c:
            self.s.admission_action(a["id"], "submit")
        self.assertGreater(len(c.exception.errors), 3)

    def test_no_lots_before_approval_and_locked_after_submit(self):
        a = self.admission(approve=False)
        self.assertEqual(self.s.list_lots(), [])
        with self.assertRaises(ApiError):
            self.s.save_admission({}, a["id"])

    def test_approve_requires_reference(self):
        a = self.admission(approve=False)
        with self.assertRaises(ApiError):
            self.s.admission_action(a["id"], "approve", {})

    def test_reject_then_edit(self):
        a = self.admission(approve=False)
        self.s.admission_action(a["id"], "reject", {"reason": "bad HTS"})
        b = self.s.save_admission({"zone_id": self.zone, "lines": [line()]}, a["id"])
        self.assertEqual(b["status"], "draft")


class TestWithdrawals(Base):
    def test_pf_duty_locked_rate(self):
        lot = self.lot()
        r = self.s.withdraw({"lot_id": lot["id"], "kind": "consumption", "qty": 40, "date": TODAY, "entry_no": "E1"})
        self.assertEqual(r["estimated_duty"], 20.0)  # 40 * $10 * 5%
        self.assertEqual(r["lot"]["qty_on_hand"], 60)

    def test_npf_needs_rate(self):
        lot = self.lot(zone_status="NPF", duty_rate=None)
        with self.assertRaises(ApiError):
            self.s.withdraw({"lot_id": lot["id"], "kind": "consumption", "qty": 1, "date": TODAY, "entry_no": "E1"})
        r = self.s.withdraw({"lot_id": lot["id"], "kind": "consumption", "qty": 10, "date": TODAY,
                             "entry_no": "E1", "duty_rate": 2.5})
        self.assertEqual(r["estimated_duty"], 2.5)

    def test_zr_cannot_enter_commerce_but_can_export(self):
        lot = self.lot(zone_status="ZR", duty_rate=None)
        with self.assertRaises(ApiError):
            self.s.withdraw({"lot_id": lot["id"], "kind": "consumption", "qty": 1, "date": TODAY, "entry_no": "E1"})
        self.s.withdraw({"lot_id": lot["id"], "kind": "export", "qty": 1, "date": TODAY, "export_ref": "ITN1"})

    def test_transfer_moves_lot_duty_free_and_weekly_report(self):
        lot = self.lot()
        z2 = self.s.create_zone({"zone_no": "FTZ 2", "name": "Two"})["id"]
        self.s.withdraw({"lot_id": lot["id"], "kind": "transfer", "qty": 30, "date": TODAY, "dest_zone_id": z2, "export_ref": "T1"})
        by_zone = {x["zone_id"]: x for x in self.s.list_lots()}
        self.assertEqual((by_zone[self.zone]["qty_on_hand"], by_zone[z2]["qty_on_hand"]), (70, 30))
        self.assertEqual(by_zone[z2]["zone_status"], "PF")
        with self.assertRaises(ApiError):
            self.s.withdraw({"lot_id": lot["id"], "kind": "transfer", "qty": 1, "date": TODAY, "dest_zone_id": self.zone, "export_ref": "T"})
        self.s.withdraw({"lot_id": lot["id"], "kind": "consumption", "qty": 10, "date": TODAY, "entry_no": "E9"})
        wk = self.s.reports()["weekly_entries"]
        self.assertEqual((wk[0]["withdrawals"], wk[0]["est_duty"], wk[0]["value"]), (1, 5.0, 100.0))

    def test_cannot_overdraw(self):
        lot = self.lot()
        with self.assertRaises(ApiError):
            self.s.withdraw({"lot_id": lot["id"], "kind": "export", "qty": 101, "date": TODAY, "export_ref": "x"})


class TestPermitsAndActivities(Base):
    def test_blanket_max_12_months(self):
        with self.assertRaises(ApiError):
            self.s.save_permit({"zone_id": self.zone, "operator_id": self.op, "kind": "blanket",
                                "activities": ["destroy"], "valid_from": TODAY, "valid_to": days(500)})

    def test_activity_requires_active_covering_permit(self):
        lot = self.lot()
        draft = self.s.save_permit({"zone_id": self.zone, "operator_id": self.op, "kind": "individual",
                                    "activities": ["destroy"], "valid_from": days(-1), "valid_to": days(5)})
        base = {"lot_id": lot["id"], "kind": "destroy", "qty": 1, "performed_on": TODAY, "note": "crushed"}
        with self.assertRaises(ApiError):  # not yet active
            self.s.perform_activity({**base, "permit_id": draft["id"]})
        self.s.permit_action(draft["id"], "activate", {"cbp_ref": "X"})
        with self.assertRaises(ApiError):  # activity not covered
            self.s.perform_activity({**base, "permit_id": draft["id"], "kind": "manipulate"})
        with self.assertRaises(ApiError):  # outside dates
            self.s.perform_activity({**base, "permit_id": draft["id"], "performed_on": days(30)})
        self.s.perform_activity({**base, "permit_id": draft["id"]})
        self.assertEqual(self.s.list_lots()[0]["qty_on_hand"], 99)

    def test_manufacture_creates_finished_lot(self):
        lot = self.lot()
        p = self.permit()
        self.s.perform_activity({"permit_id": p["id"], "lot_id": lot["id"], "kind": "manufacture", "qty": 50,
                                 "performed_on": TODAY, "output_qty": 25, "output_desc": "Kits",
                                 "output_htsus": "9503.00.0073", "output_uom": "SET"})
        lots = {x["description"]: x for x in self.s.list_lots()}
        self.assertEqual(lots["Widgets"]["qty_on_hand"], 50)
        self.assertEqual(lots["Kits"]["qty_on_hand"], 25)
        self.assertEqual(lots["Kits"]["unit_value"], 20)  # $500 of input over 25 sets
        self.assertEqual(lots["Kits"]["zone_status"], "PF")

    def test_temp_removal_and_return(self):
        lot = self.lot()
        p = self.permit()
        a = self.s.perform_activity({"permit_id": p["id"], "lot_id": lot["id"], "kind": "temp_removal", "qty": 30,
                                     "performed_on": TODAY, "expected_return": days(5)})
        x = self.s.list_lots()[0]
        self.assertEqual((x["qty_on_hand"], x["qty_out"]), (70, 30))
        self.s.return_removal(a["id"], {"qty": 10, "date": TODAY})
        self.assertEqual(self.s.list_activities()[0]["status"], "open")
        self.s.return_removal(a["id"], {"qty": 20, "date": TODAY})
        x = self.s.list_lots()[0]
        self.assertEqual((x["qty_on_hand"], x["qty_out"]), (100, 0))
        self.assertEqual(self.s.list_activities()[0]["status"], "returned")
        with self.assertRaises(ApiError):
            self.s.return_removal(a["id"], {"qty": 1, "date": TODAY})


class TestInbond(Base):
    def ib(self, lot, typ="TE", qty=40, **kw):
        d = dict(type=typ, zone_id=self.zone, carrier_id=self.car, mode="truck", origin_port="1001",
                 dest_port="1001" if typ == "IE" else "2002", bl_no="B1", issued_date=TODAY,
                 lines=[{"lot_id": lot["id"], "qty": qty}])
        d.update(kw)
        return self.s.create_inbond(d)

    def test_issue_deducts_stock_and_sets_due_date(self):
        b = self.ib(self.lot())
        self.assertEqual(b["due_date"], days(30))
        self.assertEqual(b["code"], "62")
        self.assertEqual(self.s.list_lots()[0]["qty_on_hand"], 60)
        self.assertEqual(b["total_value"], 400)

    def test_cannot_over_issue(self):
        with self.assertRaises(ApiError):
            self.ib(self.lot(), qty=101)

    def test_te_closes_only_with_export_proof(self):
        b = self.ib(self.lot())
        with self.assertRaises(ApiError):
            self.s.inbond_action(b["id"], "close", {"date": TODAY})  # not arrived
        self.s.inbond_action(b["id"], "arrive", {"date": TODAY})
        with self.assertRaises(ApiError):
            self.s.inbond_action(b["id"], "close", {"date": TODAY})
        c = self.s.inbond_action(b["id"], "close", {"date": TODAY, "export_carrier": "MSC", "foreign_dest": "Rotterdam"})
        self.assertEqual(c["status"], "closed")

    def test_it_closes_on_entry_number(self):
        b = self.ib(self.lot(), "IT")
        self.s.inbond_action(b["id"], "arrive", {"date": TODAY})
        with self.assertRaises(ApiError):
            self.s.inbond_action(b["id"], "close", {"date": TODAY})
        self.assertEqual(self.s.inbond_action(b["id"], "close", {"date": TODAY, "entry_no": "X12"})["entry_no"], "X12")

    def test_ie_same_port(self):
        b = self.ib(self.lot(), "IE")
        self.assertEqual((b["origin_port"], b["dest_port"], b["code"]), ("1001", "1001", "63"))

    def test_zr_blocked_from_it(self):
        with self.assertRaises(ApiError):
            self.ib(self.lot(zone_status="ZR", duty_rate=None), "IT")

    def test_cancel_restores_stock_only_before_departure(self):
        b = self.ib(self.lot())
        self.s.inbond_action(b["id"], "cancel")
        self.assertEqual(self.s.list_lots()[0]["qty_on_hand"], 100)
        b2 = self.ib(self.s.list_lots()[0])
        self.s.inbond_action(b2["id"], "depart", {"date": TODAY})
        with self.assertRaises(ApiError):
            self.s.inbond_action(b2["id"], "cancel")

    def test_overdue_and_late_arrival(self):
        b = self.ib(self.lot(), issued_date=days(-40))
        self.assertTrue(self.s.get_inbond(b["id"])["overdue"])
        self.s.inbond_action(b["id"], "arrive", {"date": TODAY})
        got = self.s.get_inbond(b["id"])
        self.assertFalse(got["overdue"])
        self.assertTrue(got["late_arrival"])

    def test_manual_line_for_non_ftz_cargo(self):
        b = self.s.create_inbond(dict(type="IT", zone_id=self.zone, carrier_id=self.car, mode="rail", origin_port="1001",
                                      dest_port="3003", bl_no="B", issued_date=TODAY, lines=[line()]))
        self.assertIsNone(b["lines"][0]["lot_id"])


class TestAtomicityAndHttp(Base):
    def test_failed_inbond_leaves_stock_untouched(self):
        lot = self.lot()
        with self.assertRaises(ApiError):
            self.s.create_inbond(dict(type="TE", zone_id=self.zone, carrier_id=self.car, mode="truck", origin_port="1001",
                                      dest_port="2002", bl_no="B", issued_date=TODAY,
                                      lines=[{"lot_id": lot["id"], "qty": 10}, {"lot_id": lot["id"], "qty": 95}]))
        self.assertEqual(self.s.list_lots()[0]["qty_on_hand"], 100)

    def test_http_round_trip(self):
        from ftz import server
        os.environ["FTZ_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
        os.environ["FTZ_QUIET"] = "1"
        srv = server.make_server("127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def call(method, url, body=None):
            req = urllib.request.Request(base + url, method=method, headers={"X-User": "http"},
                                         data=json.dumps(body).encode() if body is not None else None)
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as ex:
                return ex.code, ex.read()
        try:
            self.assertEqual(call("POST", "/api/zones", {"zone_no": "Z1", "name": "N"})[0], 200)
            self.assertEqual(call("POST", "/api/admissions", {})[0], 200)
            st, b = call("POST", "/api/admissions/1/submit", {})
            self.assertEqual(st, 400)
            self.assertIn("errors", json.loads(b))
            self.assertEqual(call("GET", "/print/admission/1")[0], 200)
            self.assertEqual(call("GET", "/export/lots.csv")[0], 200)
            self.assertEqual(call("GET", "/export/sqlite_master.csv")[0], 404)
            self.assertEqual(call("GET", "/../../etc/passwd")[0], 404)
            self.assertEqual(call("GET", "/backup")[0], 200)
        finally:
            srv.shutdown()


class TestLogin(unittest.TestCase):
    def test_basic_auth_required_and_user_recorded(self):
        import base64
        from ftz import server
        os.environ["FTZ_DB"] = os.path.join(tempfile.mkdtemp(), "a.db")
        os.environ["FTZ_QUIET"] = "1"
        os.environ["FTZ_USERS"] = "alice:s3cret;bob:pw"
        srv = server.make_server("127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"

        def call(url, auth=None, method="GET", body=None):
            h = {"X-User": "spoof"}
            if auth:
                h["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
            req = urllib.request.Request(base + url, method=method, headers=h, data=body)
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as ex:
                return ex.code, ex.read()
        try:
            self.assertEqual(call("/")[0], 401)
            self.assertEqual(call("/api/lookups")[0], 401)
            self.assertEqual(call("/api/lookups", "alice:wrong")[0], 401)
            self.assertEqual(call("/healthz")[0], 200)
            self.assertEqual(call("/api/lookups", "alice:s3cret")[0], 200)
            self.assertEqual(call("/", "bob:pw")[0], 200)
            call("/api/zones", "alice:s3cret", "POST", b'{"zone_no":"Z9","name":"N"}')
            log = json.loads(call("/api/audit", "alice:s3cret")[1])
            self.assertEqual(log[0]["user"], "alice")  # not the spoofed X-User header
        finally:
            srv.shutdown()
            del os.environ["FTZ_USERS"]


if __name__ == "__main__":
    unittest.main()
