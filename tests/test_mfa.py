"""Two-factor sign-in: RFC 6238 vectors, enrolment, login, replay, recovery codes, admin reset."""
import json
import time
import unittest

from ftz import mfa
from tests import test_hardening as hard

RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"          # ASCII "12345678901234567890", the RFC 6238 test key


class TestTotp(unittest.TestCase):
    def test_rfc_6238_vectors(self):
        for t, code in ((59, "287082"), (1111111109, "081804"), (1111111111, "050471"), (1234567890, "005924"), (2000000000, "279037")):
            self.assertEqual(mfa.code_at(RFC_SECRET, t // 30), code, t)

    def test_window_and_replay(self):
        now = 1111111109
        step = now // 30
        self.assertEqual(mfa.verify(RFC_SECRET, "081804", 0, now), step)
        self.assertIsNone(mfa.verify(RFC_SECRET, "081804", step, now))                  # a used code does not work again
        self.assertEqual(mfa.verify(RFC_SECRET, mfa.code_at(RFC_SECRET, step - 1), 0, now), step - 1)   # one step of clock drift is fine
        self.assertIsNone(mfa.verify(RFC_SECRET, mfa.code_at(RFC_SECRET, step - 5), 0, now))            # five steps is not
        for bad in ("", "12345", "abcdef", "0818041", None):
            self.assertIsNone(mfa.verify(RFC_SECRET, bad, 0, now))

    def test_uri_and_recovery_codes(self):
        self.assertTrue(mfa.uri("a b", "ABC").startswith("otpauth://totp/Globlexus%20FTZ%20Control:a%20b?secret=ABC"))
        codes, stored = mfa.new_recovery_codes()
        self.assertEqual(len(set(codes)), 8)
        left = mfa.use_recovery(stored, codes[0].upper())
        self.assertEqual(len(json.loads(left)), 7)
        self.assertIsNone(mfa.use_recovery(left, codes[0]))                              # one use each
        self.assertIsNone(mfa.use_recovery(stored, "000000"))
        self.assertIsNone(mfa.use_recovery("not json", codes[1]))


class TestMfaFlow(hard.SiteCase):
    def setup_user(self):
        s = self.start()
        s.login("owner", "owner-pass-12345")
        self.assertEqual(s.call("POST", "/api/users", {"username": "carla", "password": "Zebra-Quartz-2026!", "role": "user"})[0], 200)
        s.cookie = None
        s.login("carla", "Zebra-Quartz-2026!")
        return s

    def enrol(self, s):
        st, body, _ = s.call("POST", "/auth/mfa/begin", {})
        self.assertEqual(st, 200)
        secret = json.loads(body)["secret"].replace(" ", "")
        st, body, _ = s.call("POST", "/auth/mfa/enable", {"code": mfa.code_at(secret, int(time.time() // 30))})
        self.assertEqual(st, 200, body)
        return secret, json.loads(body)["recovery_codes"]

    def test_enrol_then_login_needs_the_code(self):
        s = self.setup_user()
        self.assertEqual(s.call("POST", "/auth/mfa/enable", {"code": "000000"})[0], 400)       # not started
        st, body, _ = s.call("POST", "/auth/mfa/begin", {})
        secret = json.loads(body)["secret"].replace(" ", "")
        self.assertEqual(s.call("POST", "/auth/mfa/enable", {"code": "000000"})[0], 400)       # wrong code leaves it off
        self.assertFalse(json.loads(s.call("GET", "/auth/me")[1])["mfa"])
        step = int(time.time() // 30)
        self.assertEqual(s.call("POST", "/auth/mfa/enable", {"code": mfa.code_at(secret, step)})[0], 200)
        self.assertTrue(json.loads(s.call("GET", "/auth/me")[1])["mfa"])
        s.cookie = None
        st, body, _ = s.login("carla", "Zebra-Quartz-2026!")
        self.assertEqual((st, json.loads(body)), (200, {"mfa_required": True}))                # no session yet
        self.assertIsNone(s.cookie)
        self.assertEqual(s.call("GET", "/api/lots")[0], 401)
        self.assertEqual(s.call("POST", "/auth/login", {"username": "carla", "password": "Zebra-Quartz-2026!", "code": "123456"})[0], 401)
        self.assertEqual(s.call("POST", "/auth/login", {"username": "carla", "password": "wrong", "code": mfa.code_at(secret, step + 1)})[0], 401)
        st, _, _ = s.call("POST", "/auth/login", {"username": "carla", "password": "Zebra-Quartz-2026!", "code": mfa.code_at(secret, step + 1)})
        self.assertEqual(st, 200)

    def test_a_code_cannot_be_reused_and_recovery_codes_work_once(self):
        s = self.setup_user()
        secret, recovery = self.enrol(s)
        s.cookie = None
        nxt = mfa.code_at(secret, int(time.time() // 30) + 1)
        self.assertEqual(s.call("POST", "/auth/login", {"username": "carla", "password": "Zebra-Quartz-2026!", "code": nxt})[0], 200)
        self.assertEqual(s.call("POST", "/auth/login", {"username": "carla", "password": "Zebra-Quartz-2026!", "code": nxt})[0], 401)     # replay
        self.assertEqual(s.call("POST", "/auth/login", {"username": "carla", "password": "Zebra-Quartz-2026!", "code": recovery[0]})[0], 200)
        self.assertEqual(s.call("POST", "/auth/login", {"username": "carla", "password": "Zebra-Quartz-2026!", "code": recovery[0]})[0], 401)

    def test_wrong_codes_are_rate_limited(self):
        s = self.setup_user()
        self.enrol(s)
        s.cookie = None
        for _ in range(5):
            self.assertEqual(s.call("POST", "/auth/login", {"username": "carla", "password": "Zebra-Quartz-2026!", "code": "111111"})[0], 401)
        self.assertEqual(s.call("POST", "/auth/login", {"username": "carla", "password": "Zebra-Quartz-2026!", "code": "111111"})[0], 429)

    def test_turning_it_off_needs_password_and_code(self):
        s = self.setup_user()
        secret, recovery = self.enrol(s)
        self.assertEqual(s.call("POST", "/auth/mfa/disable", {"password": "wrong", "code": recovery[0]})[0], 400)
        self.assertEqual(s.call("POST", "/auth/mfa/disable", {"password": "Zebra-Quartz-2026!", "code": "000000"})[0], 400)
        self.assertEqual(s.call("POST", "/auth/mfa/disable", {"password": "Zebra-Quartz-2026!", "code": recovery[1]})[0], 200)
        self.assertFalse(json.loads(s.call("GET", "/auth/me")[1])["mfa"])
        s.cookie = None
        self.assertEqual(s.login("carla", "Zebra-Quartz-2026!")[0], 200)

    def test_admin_can_reset_a_lost_device(self):
        s = self.setup_user()
        self.enrol(s)
        s.cookie = None
        s.login("owner", "owner-pass-12345")
        uid = next(u["id"] for u in json.loads(s.call("GET", "/api/users")[1]) if u["username"] == "carla")
        self.assertTrue(next(u for u in json.loads(s.call("GET", "/api/users")[1]) if u["id"] == uid)["mfa"])
        self.assertEqual(s.call("POST", f"/api/users/{uid}/reset-2fa", {})[0], 200)
        s.cookie = None
        self.assertEqual(s.login("carla", "Zebra-Quartz-2026!")[0], 200)
        self.assertEqual(s.call("POST", f"/api/users/{uid}/reset-2fa", {})[0], 403)          # ordinary users cannot reset anyone's 2FA
        s.cookie = None
        s.login("owner", "owner-pass-12345")
        self.assertEqual(s.call("POST", "/api/users/9999/reset-2fa", {})[0], 404)

    def test_demo_account_cannot_enrol(self):
        s = self.start(FTZ_DEMO_MODE="true")
        cfg = json.loads(s.call("GET", "/auth/config")[1])["demo"]
        s.login(cfg["username"], cfg["password"])
        self.assertEqual(s.call("POST", "/auth/mfa/begin", {})[0], 400)


if __name__ == "__main__":
    unittest.main()
