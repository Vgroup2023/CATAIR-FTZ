"""Security headers, deep health check and print-page CSP."""
import json
import re
import unittest

from ftz import auth
from tests import test_ftz as base


class SiteCase(unittest.TestCase):
    """Same helper as test_ftz.TestSite, without inheriting its tests."""
    def tearDown(self):
        getattr(self, "site", None) and self.site.close()

    def start(self, **env):
        auth.LIMITS.h.clear()
        self.site = base.Site(FTZ_AUTH_USER="owner", FTZ_AUTH_PASSWORD="owner-pass-12345", **env)
        return self.site


class TestHeaders(SiteCase):
    def test_every_response_carries_the_security_headers(self):
        s = self.start()
        for path in ("/", "/healthz", "/api/lots", "/offline.html"):
            _, _, r = s.call("GET", path)
            self.assertIn("script-src 'self'", r.getheader("Content-Security-Policy"), path)
            self.assertIn("frame-ancestors 'none'", r.getheader("Content-Security-Policy"), path)
            self.assertEqual(r.getheader("X-Frame-Options"), "DENY")
            self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
            self.assertIn("camera=()", r.getheader("Permissions-Policy"))
            self.assertEqual(r.getheader("Cross-Origin-Opener-Policy"), "same-origin")

    def test_hsts_only_over_https(self):
        s = self.start()
        self.assertIsNone(s.call("GET", "/")[2].getheader("Strict-Transport-Security"))
        self.site.close()
        s = self.start(FTZ_COOKIE_SECURE="1")
        self.assertIn("max-age=31536000", s.call("GET", "/")[2].getheader("Strict-Transport-Security"))

    def test_no_page_uses_inline_script_or_handlers(self):
        import os
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ftz", "static")
        for name in os.listdir(d):
            if name.endswith(".html"):
                with open(os.path.join(d, name)) as fh:
                    text = fh.read()
                self.assertIsNone(re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text), name + " has an inline script")
                self.assertIsNone(re.search(r"\son\w+\s*=", text), name + " has an inline event handler")

    def test_deep_health_check(self):
        s = self.start()
        st, body, _ = s.call("GET", "/healthz?deep=1")
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(body)["ok"])
        self.assertEqual(s.call("GET", "/healthz")[1], b"ok")

    def test_print_pages_get_a_nonce_policy_and_a_working_script(self):
        s = self.start()
        s.login("owner", "owner-pass-12345")
        s.call("POST", "/api/zones", {"zone_no": "FTZ 1", "name": "Z"})
        s.call("POST", "/api/admissions", {"zone_id": 1, "lines": [{"description": "x", "qty": 1, "uom": "PCS", "value": 1, "zone_status": "NPF"}]})
        st, body, r = s.call("GET", "/print/admission/1")
        self.assertEqual(st, 200)
        csp = r.getheader("Content-Security-Policy")
        nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
        self.assertIn(f"nonce={nonce}".encode(), body)
        self.assertNotIn(b"__NONCE__", body)
        self.assertNotIn("unsafe-inline'; img", csp.split("script-src")[1][:30])


if __name__ == "__main__":
    unittest.main()


class TestAuditorRole(SiteCase):
    def test_auditor_can_read_and_export_but_not_change(self):
        s = self.start()
        s.login("owner", "owner-pass-12345")
        self.assertEqual(s.call("POST", "/api/zones", {"zone_no": "FTZ 1", "name": "Z"})[0], 200)
        self.assertEqual(s.call("POST", "/api/users", {"username": "auditor1", "password": "Audit-pass-2026!", "role": "auditor"})[0], 200)
        s.cookie = None
        self.assertEqual(s.login("auditor1", "Audit-pass-2026!")[0], 200)
        self.assertEqual(json.loads(s.call("GET", "/auth/me")[1])["role"], "auditor")
        for path in ("/api/lookups", "/api/lots", "/api/audit", "/api/integrity", "/api/ft"):
            self.assertEqual(s.call("GET", path)[0], 200, path)
        self.assertEqual(s.call("GET", "/export/lots.csv")[0], 200)
        for method, path in (("POST", "/api/zones"), ("POST", "/api/admissions"), ("POST", "/api/withdrawals"), ("POST", "/api/ft"), ("PUT", "/api/settings"), ("POST", "/api/parts")):
            st, body, _ = s.call(method, path, {"zone_no": "FTZ 2", "name": "Y"})
            self.assertEqual(st, 403, path)
            self.assertIn("read-only", body.decode())
        self.assertEqual(s.call("POST", "/api/ft/check", {"data": {}})[0], 200)
        self.assertEqual(s.call("GET", "/api/users")[0], 403)
        self.assertEqual(s.call("GET", "/backup")[0], 403)
        self.assertEqual(s.call("POST", "/auth/logout")[0], 200)

    def test_only_valid_roles_can_be_created(self):
        s = self.start()
        s.login("owner", "owner-pass-12345")
        self.assertEqual(s.call("POST", "/api/users", {"username": "x1x1x1", "password": "Audit-pass-2026!", "role": "superuser"})[0], 400)


class TestPublicPages(SiteCase):
    def test_security_and_privacy_pages_are_public_and_linked(self):
        s = self.start()
        for path, needle in (("/security", b"Security and data handling"), ("/privacy", b"Privacy notice")):
            st, body, r = s.call("GET", path)
            self.assertEqual(st, 200, path)
            self.assertIn(needle, body)
            self.assertIn("script-src 'self'", r.getheader("Content-Security-Policy"))
        front = s.call("GET", "/")[1]
        self.assertIn(b'href="/security"', front)
        self.assertIn(b'href="/privacy"', front)
