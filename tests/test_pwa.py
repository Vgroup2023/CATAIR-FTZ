"""Installable-app files: reachable before sign-in, correct, and incapable of exposing private data."""
import json
import os
import struct
import unittest

os.environ.setdefault("FTZ_PBKDF2_ITER", "1000")

from ftz import auth
from tests.test_ftz import Site


def png_size(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


class TestInstallableApp(unittest.TestCase):
    def setUp(self):
        auth.LIMITS.h.clear()
        self.site = Site(FTZ_AUTH_USER="owner", FTZ_AUTH_PASSWORD="owner-pass-12345")

    def tearDown(self):
        self.site.close()

    def test_manifest_is_public_and_complete(self):
        st, body, r = self.site.call("GET", "/manifest.webmanifest")
        self.assertEqual(st, 200)
        self.assertIn("manifest+json", r.getheader("Content-Type"))
        m = json.loads(body)
        self.assertEqual((m["display"], m["start_url"], m["scope"]), ("standalone", "/", "/"))
        self.assertTrue(m["name"] and m["short_name"] and m["theme_color"] and m["background_color"])
        purposes = {i["purpose"] for i in m["icons"]}
        self.assertEqual(purposes, {"any", "maskable"})
        for icon in m["icons"]:                                   # every declared icon exists at its declared size
            st, data, _ = self.site.call("GET", icon["src"])
            self.assertEqual(st, 200, icon["src"])
            w, h = icon["sizes"].split("x")
            self.assertEqual(png_size(data), (int(w), int(h)), icon["src"])
        self.assertEqual(png_size(self.site.call("GET", "/apple-touch-icon.png")[1]), (180, 180))

    def test_service_worker_and_offline_page_are_public(self):
        st, sw, r = self.site.call("GET", "/sw.js")
        self.assertEqual(st, 200)
        self.assertIn("javascript", r.getheader("Content-Type"))
        text = sw.decode()
        self.assertIn("/offline.html", text)
        self.assertIn("(api|auth|print|export)", text)            # private paths are never intercepted or stored
        self.assertIn('p === "/backup"', text)
        self.assertEqual(self.site.call("GET", "/offline.html")[0], 200)
        self.assertEqual(self.site.call("GET", "/pwa.js")[0], 200)

    def test_service_worker_never_stores_pages(self):
        text = self.site.call("GET", "/sw.js")[1].decode()
        nav = text[text.index('req.mode === "navigate"'):]
        nav = nav[:nav.index("return;")]
        self.assertNotIn("cache", nav.replace("caches.match", ""))   # navigations are fetched live, never put in a cache

    def test_private_things_stay_private(self):
        for path, expect in (("/app.js", 401), ("/ft.js", 401), ("/api/lookups", 401), ("/api/audit", 401), ("/backup", 302), ("/print/admission/1", 302), ("/export/lots.csv", 302)):
            self.assertEqual(self.site.call("GET", path)[0], expect, path)

    def test_pages_link_the_manifest(self):
        for path in ("/", "/reset"):
            body = self.site.call("GET", path)[1]
            self.assertIn(b'rel="manifest"', body, path)
            self.assertIn(b"apple-touch-icon", body, path)
        self.assertIn(b"data-install", self.site.call("GET", "/")[1])          # the sign-in page offers "Install"
        self.site.login("owner", "owner-pass-12345")
        app = self.site.call("GET", "/")[1]
        self.assertIn(b'rel="manifest"', app)
        self.assertIn(b"pwa.js", app)
        self.assertIn(b"data-install", app)


if __name__ == "__main__":
    unittest.main()
