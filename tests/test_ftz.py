import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import date, timedelta

os.environ.setdefault('FTZ_PBKDF2_ITER', '1000')  # fast hashing for tests

from ftz import auth, db, rules
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

    def admission(self, lines=None, approve=True, entry_date=None, bl="BL1"):
        a = self.s.save_admission({
            "zone_id": self.zone, "operator_id": self.op, "carrier_id": self.car, "importer_id": self.imp,
            "transport_mode": "truck", "transport_doc": bl, "port_of_entry": "1001", "entry_date": entry_date or TODAY,
            "lines": lines or [line()]})
        self.s.admission_action(a["id"], "submit")
        if approve:
            a = self.s.admission_action(a["id"], "approve", {"cbp_ref": "ACE-1"})
        return a

    def lot(self, entry_date=None, **kw):
        self.admission([line(**kw)], entry_date=entry_date)
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
        b = self.ib(self.lot(entry_date=days(-60)), issued_date=days(-40))
        self.assertTrue(self.s.get_inbond(b["id"])["overdue"])
        self.s.inbond_action(b["id"], "arrive", {"date": TODAY})
        got = self.s.get_inbond(b["id"])
        self.assertFalse(got["overdue"])
        self.assertTrue(got["late_arrival"])

    def test_manual_line_for_non_ftz_cargo(self):
        b = self.s.create_inbond(dict(type="IT", zone_id=self.zone, carrier_id=self.car, mode="rail", origin_port="1001",
                                      dest_port="3003", bl_no="B", issued_date=TODAY, lines=[line()]))
        self.assertIsNone(b["lines"][0]["lot_id"])


class TestAtomicity(Base):
    def test_failed_inbond_leaves_stock_untouched(self):
        lot = self.lot()
        with self.assertRaises(ApiError):
            self.s.create_inbond(dict(type="TE", zone_id=self.zone, carrier_id=self.car, mode="truck", origin_port="1001",
                                      dest_port="2002", bl_no="B", issued_date=TODAY,
                                      lines=[{"lot_id": lot["id"], "qty": 10}, {"lot_id": lot["id"], "qty": 95}]))
        self.assertEqual(self.s.list_lots()[0]["qty_on_hand"], 100)


class TestAccounts(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        db.init(self.con)
        for k in ("FTZ_SMTP_HOST", "FTZ_BASE_URL"):
            os.environ.pop(k, None)
        auth.LIMITS.h.clear()

    def reset_link(self, ident):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            auth.start_reset(self.con, ident, "http://x")
        out = buf.getvalue()
        return out.split("token=")[1].strip() if "token=" in out else None

    def test_password_policy(self):
        self.assertTrue(auth.policy_errors("short"))
        self.assertTrue(auth.policy_errors("password123"))
        self.assertTrue(auth.policy_errors("alice-is-great", "alice"))
        self.assertFalse(auth.policy_errors("correct-horse-battery"))

    def test_hashing_and_authentication(self):
        auth.create_user(self.con, "alice", "correct-horse-battery", "a@x.com")
        stored = self.con.execute("SELECT password_hash FROM users").fetchone()[0]
        self.assertNotIn("correct-horse", stored)
        self.assertTrue(auth.authenticate(self.con, "ALICE", "correct-horse-battery"))
        self.assertIsNone(auth.authenticate(self.con, "alice", "wrong"))
        self.assertIsNone(auth.authenticate(self.con, "nobody", "whatever"))

    def test_reset_flow_single_use_and_signs_out(self):
        uid = auth.create_user(self.con, "alice", "correct-horse-battery", "a@x.com")
        tok = auth.new_session(self.con, uid)
        link = self.reset_link("a@x.com")            # by email
        self.assertTrue(link)
        with self.assertRaises(ApiError):
            auth.complete_reset(self.con, link, "short")   # policy still applies
        self.assertEqual(auth.complete_reset(self.con, link, "brand-new-passphrase"), "alice")
        self.assertTrue(auth.authenticate(self.con, "alice", "brand-new-passphrase"))
        self.assertIsNone(auth.authenticate(self.con, "alice", "correct-horse-battery"))
        self.assertIsNone(auth.session_user(self.con, tok))  # old sessions ended
        with self.assertRaises(ApiError):
            auth.complete_reset(self.con, link, "another-new-passphrase")  # single use

    def test_new_request_revokes_old_link_and_expiry(self):
        auth.create_user(self.con, "alice", "correct-horse-battery", "a@x.com")
        first = self.reset_link("alice")
        second = self.reset_link("alice")
        with self.assertRaises(ApiError):
            auth.complete_reset(self.con, first, "brand-new-passphrase")
        self.con.execute("UPDATE reset_tokens SET expires_at='2000-01-01T00:00:00'")
        with self.assertRaises(ApiError):
            auth.complete_reset(self.con, second, "brand-new-passphrase")

    def test_unknown_demo_and_disabled_accounts_get_no_link(self):
        auth.create_user(self.con, "demo-admin", "Demo-FTZ-2026!", None, role="admin", is_demo=1, check_policy=False)
        uid = auth.create_user(self.con, "bob", "correct-horse-battery", "b@x.com")
        self.assertIsNone(self.reset_link("nobody"))
        self.assertIsNone(self.reset_link("demo-admin"))
        self.con.execute("UPDATE users SET active=0 WHERE id=?", (uid,))
        self.assertIsNone(self.reset_link("bob"))

    def test_tokens_stored_hashed(self):
        auth.create_user(self.con, "alice", "correct-horse-battery")
        link = self.reset_link("alice")
        self.assertNotIn(link, self.con.execute("SELECT token_hash FROM reset_tokens").fetchone()[0])

    def test_last_admin_protected(self):
        a = auth.create_user(self.con, "root", "correct-horse-battery", role="admin")
        actor = dict(self.con.execute("SELECT * FROM users WHERE id=?", (a,)).fetchone())
        with self.assertRaises(ApiError):
            auth.admin_set_active(self.con, actor, a, False)  # cannot disable yourself / last admin

    def test_seed_users_bootstrap_and_demo(self):
        os.environ["FTZ_DEMO_MODE"] = "1"
        try:
            notes = auth.seed_users(self.con)
        finally:
            del os.environ["FTZ_DEMO_MODE"]
        self.assertTrue(any("one-time password" in n for n in notes))            # no real account existed
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM users WHERE is_demo=1 AND active=1").fetchone()[0], 1)
        auth.seed_users(self.con)                                                # demo off: account disabled
        self.assertEqual(self.con.execute("SELECT active FROM users WHERE is_demo=1").fetchone()[0], 0)


class Site:
    """Runs the real HTTP server on a temp database and talks to it with a cookie."""
    def __init__(self, **env):
        import http.client
        self.http = http.client
        os.environ.update({"FTZ_DB": os.path.join(tempfile.mkdtemp(), "t.db"), "FTZ_QUIET": "1", **env})
        self.keys = list(env) + ["FTZ_DB", "FTZ_QUIET"]
        from ftz import server
        self.srv = server.make_server("127.0.0.1", 0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port, self.cookie = self.srv.server_address[1], None

    def call(self, method, path, body=None, headers=None, raw=None):
        c = self.http.HTTPConnection("127.0.0.1", self.port)
        h = {"Content-Type": "application/json", **(headers or {})}
        if self.cookie:
            h["Cookie"] = self.cookie
        c.request(method, path, body=raw if raw is not None else (json.dumps(body) if body is not None else None), headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        sc = r.getheader("Set-Cookie")
        if sc and path in ("/auth/login", "/auth/password") and r.status == 200:
            self.cookie = sc.split(";")[0]
        return r.status, data, r

    def login(self, user, pw):
        return self.call("POST", "/auth/login", {"username": user, "password": pw})

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        for k in self.keys:
            os.environ.pop(k, None)
        auth.LIMITS.h.clear()


class TestSite(unittest.TestCase):
    def tearDown(self):
        getattr(self, "site", None) and self.site.close()

    def start(self, **env):
        auth.LIMITS.h.clear()
        self.site = Site(FTZ_AUTH_USER="owner", FTZ_AUTH_PASSWORD="owner-pass-12345", **env)
        return self.site

    def test_front_page_and_gating(self):
        s = self.start()
        st, body, _ = s.call("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn(b"Forgot your password?", body)
        self.assertNotIn(b'id="menu"', body)                       # not the app
        self.assertEqual(s.call("GET", "/app.js")[0], 401)
        self.assertEqual(s.call("GET", "/api/lookups")[0], 401)
        self.assertEqual(s.call("GET", "/backup")[0], 302)
        self.assertEqual(s.call("GET", "/reset")[0], 200)
        self.assertEqual(s.call("GET", "/healthz")[0], 200)
        self.assertEqual(s.call("HEAD", "/healthz")[0], 200)
        self.assertEqual(s.call("GET", "/../../etc/passwd")[0], 302)
        self.assertEqual(s.call("GET", "/auth/config")[0], 200)

    def test_login_logout_and_cookie_flags(self):
        s = self.start()
        self.assertEqual(s.login("owner", "wrong")[0], 401)
        st, _, r = s.login("owner", "owner-pass-12345")
        self.assertEqual(st, 200)
        flags = r.getheader("Set-Cookie")
        self.assertIn("HttpOnly", flags)
        self.assertIn("SameSite=Lax", flags)
        self.assertIn(b'id="menu"', s.call("GET", "/")[1])           # now the app
        self.assertEqual(json.loads(s.call("GET", "/auth/me")[1])["username"], "owner")
        self.assertEqual(s.call("GET", "/api/lookups")[0], 200)
        s.call("POST", "/auth/logout", {})
        self.assertEqual(s.call("GET", "/api/lookups")[0], 401)      # server-side session is gone

    def test_audit_uses_login_not_header(self):
        s = self.start()
        s.login("owner", "owner-pass-12345")
        s.call("POST", "/api/zones", {"zone_no": "Z9", "name": "N"}, headers={"X-User": "spoof"})
        log = json.loads(s.call("GET", "/api/audit")[1])
        self.assertEqual(log[0]["user"], "owner")

    def test_lockout_after_repeated_failures(self):
        s = self.start()
        for _ in range(5):
            self.assertEqual(s.login("owner", "nope")[0], 401)
        self.assertEqual(s.login("owner", "owner-pass-12345")[0], 429)

    def test_csrf_guards(self):
        s = self.start()
        s.login("owner", "owner-pass-12345")
        self.assertEqual(s.call("POST", "/api/zones", {"zone_no": "Z1", "name": "N"}, headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(s.call("POST", "/api/zones", headers={"Content-Type": "text/plain"}, raw='{"zone_no":"Z2","name":"N"}')[0], 415)

    def test_demo_mode_shows_credentials_and_limits_the_demo_account(self):
        s = self.start(FTZ_DEMO_MODE="1")
        cfg = json.loads(s.call("GET", "/auth/config")[1])
        self.assertEqual(cfg["demo"]["username"], "demo-admin")
        st, _, _ = s.login(cfg["demo"]["username"], cfg["demo"]["password"])
        self.assertEqual(st, 200)
        self.assertGreater(len(json.loads(s.call("GET", "/api/lots")[1])), 0)          # sample data present
        self.assertEqual(s.call("GET", "/backup")[0], 403)                              # would expose password hashes
        self.assertEqual(s.call("GET", "/api/users")[0], 403)
        self.assertEqual(s.call("POST", "/api/users", {"username": "evil", "password": "correct-horse-battery"})[0], 403)
        self.assertEqual(s.call("POST", "/auth/password", {"current": cfg["demo"]["password"], "new": "hijacked-password-1"})[0], 403)

    def test_demo_credentials_hidden_when_demo_off(self):
        s = self.start()
        self.assertIsNone(json.loads(s.call("GET", "/auth/config")[1])["demo"])
        self.assertEqual(s.login("demo-admin", "Demo-FTZ-2026!")[0], 401)

    def test_forgot_password_is_uniform_and_reset_works_end_to_end(self):
        import contextlib
        import io
        s = self.start()
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            a = s.call("POST", "/auth/forgot", {"identifier": "owner"})
            b = s.call("POST", "/auth/forgot", {"identifier": "nobody-here"})
        self.assertEqual((a[0], a[1]), (b[0], b[1]))                                   # cannot tell accounts apart
        token = buf.getvalue().split("token=")[1].strip()
        self.assertEqual(s.call("POST", "/auth/reset", {"token": token, "password": "weak"})[0], 400)
        self.assertEqual(s.call("POST", "/auth/reset", {"token": token, "password": "fresh-start-passphrase"})[0], 200)
        self.assertEqual(s.login("owner", "owner-pass-12345")[0], 401)
        self.assertEqual(s.login("owner", "fresh-start-passphrase")[0], 200)
        self.assertEqual(s.call("POST", "/auth/reset", {"token": token, "password": "yet-another-passphrase"})[0], 400)

    def test_admin_manages_users_and_regular_users_cannot(self):
        s = self.start()
        s.login("owner", "owner-pass-12345")
        self.assertEqual(s.call("POST", "/api/users", {"username": "sam", "email": "sam@x.com", "password": "quiet-river-passphrase", "role": "user"})[0], 200)
        users = json.loads(s.call("GET", "/api/users")[1])
        sam = next(u for u in users if u["username"] == "sam")
        self.assertNotIn(b"password_hash", s.call("GET", "/api/users")[1])
        t = Site.__new__(Site)  # second browser for sam
        t.__dict__.update(s.__dict__)
        t.cookie = None
        self.assertEqual(t.login("sam", "quiet-river-passphrase")[0], 200)
        self.assertEqual(t.call("GET", "/api/users")[0], 403)
        self.assertEqual(t.call("GET", "/backup")[0], 403)
        self.assertEqual(s.call("POST", f"/api/users/{sam['id']}/disable", {})[0], 200)
        self.assertEqual(t.call("GET", "/api/lookups")[0], 401)                        # disabled users are cut off at once

    def test_change_own_password(self):
        s = self.start()
        s.login("owner", "owner-pass-12345")
        self.assertEqual(s.call("POST", "/auth/password", {"current": "bad", "new": "brand-new-passphrase"})[0], 400)
        self.assertEqual(s.call("POST", "/auth/password", {"current": "owner-pass-12345", "new": "brand-new-passphrase"})[0], 200)
        self.assertEqual(s.call("GET", "/api/lookups")[0], 200)                        # this browser stays signed in
        self.assertEqual(s.login("owner", "brand-new-passphrase")[0], 200)

    def test_exports_and_print_need_login_and_hide_secrets(self):
        s = self.start()
        self.assertEqual(s.call("GET", "/export/lots.csv")[0], 302)
        s.login("owner", "owner-pass-12345")
        self.assertEqual(s.call("GET", "/export/lots.csv")[0], 200)
        self.assertEqual(s.call("GET", "/export/users.csv")[0], 404)
        self.assertEqual(s.call("GET", "/export/sessions.csv")[0], 404)


if __name__ == "__main__":
    unittest.main()
