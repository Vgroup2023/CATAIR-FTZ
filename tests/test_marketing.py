"""Front-page content and client ERP connection links."""
import json
import os
import unittest

os.environ.setdefault("FTZ_PBKDF2_ITER", "1000")

from ftz import auth, db
from ftz.service import ApiError
from tests.test_ftz import Base, Site


class TestErpLinksService(Base):
    def link(self, **kw):
        d = dict(client="Acme Imports", system="Oracle NetSuite", method="api", url="https://erp.example.com/portal")
        d.update(kw)
        return self.s.save_erp_link(d)

    def test_create_update_list_delete(self):
        a = self.link(party_id=self.imp)
        self.assertEqual((a["client"], a["method"], a["active"]), ("Acme Imports", "api", 1))
        self.link(id=a["id"], url="https://erp.example.com/new", active=False, notes="paused")
        got = self.s.list_erp_links()[0]
        self.assertEqual((got["url"], got["active"], got["party_name"]), ("https://erp.example.com/new", 0, "Imp Co"))
        self.s.delete_erp_link(a["id"])
        self.assertEqual(self.s.list_erp_links(), [])
        with self.assertRaises(ApiError):
            self.s.delete_erp_link(a["id"])

    def test_client_name_can_come_from_the_linked_record(self):
        self.assertEqual(self.link(client="", party_id=self.imp)["client"], "Imp Co")

    def test_only_safe_links_are_accepted(self):
        for bad in ("http://erp.example.com", "javascript:alert(1)", "data:text/html,<b>x</b>", "ftp://x.com/a", "https://user:pw@erp.example.com",
                    "https://erp.example.com/a b", 'https://erp.example.com/"onmouseover=x', "https://erp.example.com/<script>", "//erp.example.com", "", "https://" + "a" * 600 + ".com"):
            with self.assertRaises(ApiError, msg=bad):
                self.link(url=bad)

    def test_other_field_validation(self):
        for bad in (dict(method="telepathy"), dict(system=""), dict(client=""), dict(party_id=9999), dict(notes="x" * 1001)):
            with self.assertRaises(ApiError, msg=bad):
                self.link(**bad)

    def test_changes_are_audited(self):
        self.link()
        self.assertTrue(any(a["entity"] == "erp_link" for a in self.s.audit_log()))


class TestFrontPage(unittest.TestCase):
    def setUp(self):
        auth.LIMITS.h.clear()
        self.site = Site(FTZ_AUTH_USER="owner", FTZ_AUTH_PASSWORD="owner-pass-12345", FTZ_DEMO_MODE="1")

    def tearDown(self):
        self.site.close()
        os.environ.pop("FTZ_CONTACT_EMAIL", None)

    def test_page_has_tagline_story_and_erp_section_and_keeps_the_forms(self):
        body = self.site.call("GET", "/")[1].decode()
        for text in ("Every unit accounted for.", "Every audit answered.", "Connect the ERP you already run", "Oracle NetSuite", "Microsoft Dynamics 365",
                     "Any system that can export CSV or call a web API", "FIFO that enforces itself", "Ready when CBP asks"):
            self.assertIn(text, body)
        for needed in ('id="login-form"', 'id="show-forgot"', 'id="forgot-form"', 'id="demo"', 'id="use-demo"', 'rel="manifest"'):
            self.assertIn(needed, body)

    def test_page_makes_no_claims_it_cannot_back_up(self):
        body = self.site.call("GET", "/")[1].decode().lower()
        self.assertIn("no endorsement, certification or vendor-built plug-in is implied", body)
        for claim in ("cbp-certified", "cbp certified", "ace-certified", "certified partner", "official partner", "guaranteed", "100% compliant", "testimonial"):
            self.assertNotIn(claim, body)

    def test_contact_email_is_only_offered_when_configured(self):
        self.assertIsNone(json.loads(self.site.call("GET", "/auth/config")[1])["contact"])
        os.environ["FTZ_CONTACT_EMAIL"] = "sales@example.com"
        self.assertEqual(json.loads(self.site.call("GET", "/auth/config")[1])["contact"], "sales@example.com")


class TestErpLinksHttp(unittest.TestCase):
    def setUp(self):
        auth.LIMITS.h.clear()
        self.site = Site(FTZ_AUTH_USER="owner", FTZ_AUTH_PASSWORD="owner-pass-12345", FTZ_DEMO_MODE="1")
        self.site.login("owner", "owner-pass-12345")

    def tearDown(self):
        self.site.close()

    def other(self, user, pw):
        s = Site.__new__(Site)
        s.__dict__.update(self.site.__dict__)
        s.cookie = None
        s.login(user, pw)
        return s

    def test_staff_manage_links_demo_and_tokens_cannot(self):
        st, body, _ = self.site.call("POST", "/api/erp-links", {"client": "Acme", "system": "SAP S/4HANA", "method": "csv", "url": "https://erp.example.com"})
        self.assertEqual(st, 200)
        lid = json.loads(body)["id"]
        data = json.loads(self.site.call("GET", "/api/erp-links")[1])
        self.assertIn("Odoo", data["systems"])
        self.assertIn("csv", data["methods"])
        self.assertEqual(self.site.call("POST", "/api/erp-links", {"client": "X", "system": "Odoo", "url": "http://insecure.example.com"})[0], 400)
        demo = self.other("demo-admin", "Demo-FTZ-2026!")
        self.assertEqual(demo.call("GET", "/api/erp-links")[0], 200)                                     # can look
        self.assertEqual(demo.call("POST", "/api/erp-links", {"client": "Evil", "system": "Odoo", "url": "https://evil.example.com"})[0], 403)
        self.assertEqual(demo.call("POST", f"/api/erp-links/{lid}/delete", {})[0], 403)
        tok = json.loads(self.site.call("POST", "/api/tokens", {"name": "feed"})[1])["token"]
        anon = Site.__new__(Site)
        anon.__dict__.update(self.site.__dict__)
        anon.cookie = None
        self.assertEqual(anon.call("GET", "/api/erp-links", headers={"Authorization": f"Bearer {tok}"})[0], 403)   # feeds can't read client links
        self.assertEqual(self.site.call("POST", f"/api/erp-links/{lid}/delete", {})[0], 200)

    def test_demo_data_includes_sample_links(self):
        links = json.loads(self.site.call("GET", "/api/erp-links")[1])["links"]
        self.assertTrue(links and all(l["url"].startswith("https://example.com/") for l in links))      # reserved name: harmless


if __name__ == "__main__":
    unittest.main()
