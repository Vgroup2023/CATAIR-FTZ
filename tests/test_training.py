"""Training & SOP: content stays in step with the screens, the .odt is a valid OpenDocument file, access rules."""
import io
import json
import os
import re
import unittest
import xml.dom.minidom
import zipfile

from ftz import training
from tests import test_hardening as hard

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ftz", "static")


def all_text(g=None):
    out = []
    for s in (g or training.guide())["sections"]:
        out.append(s["title"])
        for b in s["blocks"]:
            if b["t"] in ("p", "h3", "note"):
                out.append(b["text"])
            elif b["t"] in ("ul", "ol", "check"):
                out += b["items"]
            elif b["t"] == "table":
                out += b["head"] + [c for r in b["rows"] for c in r]
    return "\n".join(out)


class TestContent(unittest.TestCase):
    def test_every_menu_item_is_explained(self):
        with open(os.path.join(STATIC, "app.js")) as fh:
            nav = re.search(r"const NAV = \[(.*?)\];", fh.read(), re.S).group(1)
        labels = [m for k, m in re.findall(r'\["(\w+)", "([^"]+)"\]', nav) if k != "sep"]
        self.assertGreaterEqual(len(labels), 12)
        text = all_text()
        for label in labels:
            self.assertIn(label.replace(" · ", " · "), text, f"the guide never mentions the '{label}' menu item")

    def test_buttons_named_in_procedures_exist_in_the_app(self):
        with open(os.path.join(STATIC, "app.js")) as fh1, open(os.path.join(STATIC, "ft.js")) as fh2, open(os.path.join(STATIC, "docs.js")) as fh3:
            code = fh1.read() + fh2.read() + fh3.read()
        for label in ("+ New e214", "Run checks", "Submit to CBP", "Record CBP approval", "Record rejection", "Build CATAIR FT filing", "+ FZ action",
                      "Paste CBP response", "+ New e216 permit", "Activate permit", "+ Record activity", "Record return", "FIFO withdraw", "Adjust on-hand",
                      "+ Issue in-bond", "Depart", "Arrive", "Void", "+ Attach", "+ General document", "Run reconciliation now", "Verify history integrity",
                      "+ Part", "+ Client connection", "+ New token", "+ New user", "Reset 2FA", "Set up two-factor", "Change password", "Set email"):
            self.assertIn(label, code, f"the guide names a button the app does not have: {label}")

    def test_structure(self):
        g = training.guide()
        ids = [s["id"] for s in g["sections"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 15)
        for s in g["sections"]:
            self.assertTrue(s["title"] and s["audience"] and s["minutes"] > 0 and s["blocks"], s["id"])
            for b in s["blocks"]:
                self.assertIn(b["t"], ("p", "h3", "ul", "ol", "check", "note", "table"))
                if b["t"] == "table":
                    self.assertTrue(all(len(r) == len(b["head"]) for r in b["rows"]), f"{s['id']}: ragged table")
                    if b.get("widths"):
                        self.assertEqual(len(b["widths"]), len(b["head"]))

    def test_it_does_not_overclaim(self):
        text = all_text().lower()
        self.assertIn("does not transmit", text.replace("**", "").replace("**", ""))
        for risky in ("guarantee", "certified by cbp", "legal advice is provided"):
            self.assertNotIn(risky, text)


class TestOdt(unittest.TestCase):
    def test_valid_opendocument_package(self):
        data = training.build_odt(open(os.path.join(STATIC, "logo.png"), "rb").read())
        z = zipfile.ZipFile(io.BytesIO(data))
        names = z.namelist()
        self.assertEqual(names[0], "mimetype")
        self.assertEqual(z.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
        self.assertEqual(z.read("mimetype"), b"application/vnd.oasis.opendocument.text")
        for n in ("content.xml", "styles.xml", "meta.xml", "META-INF/manifest.xml"):
            xml.dom.minidom.parseString(z.read(n))
        manifest = z.read("META-INF/manifest.xml").decode()
        for n in names:
            if n != "mimetype" and not n.endswith("/") and n != "META-INF/manifest.xml":
                self.assertIn(f'full-path="{n}"', manifest, n)
        content = z.read("content.xml").decode()
        self.assertEqual(content.count("<text:h "), len(training.SECTIONS) + 1 + sum(1 for s in training.SECTIONS for b in s["blocks"] if b["t"] == "h3"))   # + "Contents"
        self.assertIn("SOP 1: Admit merchandise", content)

    def test_special_characters_are_escaped(self):
        self.assertEqual(training.inline("a < b & c > d"), "a &lt; b &amp; c &gt; d")
        self.assertIn('<text:span text:style-name="B">x &amp; y</text:span>', training.inline("**x & y**"))
        self.assertIn("Code", training.inline("`1 < 2`"))

    def test_date_is_in_the_document(self):
        from datetime import date
        z = zipfile.ZipFile(io.BytesIO(training.build_odt(None, date(2030, 1, 2))))
        self.assertIn("2030-01-02", z.read("content.xml").decode())
        self.assertNotIn("Pictures/logo.png", z.namelist())


class TestEndpoints(hard.SiteCase):
    def test_guide_needs_sign_in_and_downloads_as_odt(self):
        s = self.start()
        st, _, r = s.call("GET", "/training/guide.odt")
        self.assertEqual(st, 302)                                                       # signed-out users go to the sign-in page
        self.assertEqual(s.call("GET", "/api/training")[0], 401)
        self.assertEqual(s.call("GET", "/training.js")[0], 401)
        s.login("owner", "owner-pass-12345")
        st, data, r = s.call("GET", "/training/guide.odt")
        self.assertEqual(st, 200)
        self.assertEqual(r.getheader("Content-Type"), "application/vnd.oasis.opendocument.text")
        self.assertIn('attachment; filename="FTZ-Control-Training-and-SOP.odt"', r.getheader("Content-Disposition"))
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(data)))
        guide = json.loads(s.call("GET", "/api/training")[1])
        self.assertEqual(len(guide["sections"]), len(training.SECTIONS))
        self.assertEqual(s.call("GET", "/training.js")[0], 200)

    def test_auditors_can_read_the_guide(self):
        s = self.start()
        s.login("owner", "owner-pass-12345")
        s.call("POST", "/api/users", {"username": "auditor1", "password": "Audit-pass-2026!", "role": "auditor"})
        s.cookie = None
        s.login("auditor1", "Audit-pass-2026!")
        self.assertEqual(s.call("GET", "/training/guide.odt")[0], 200)
        self.assertEqual(s.call("GET", "/api/training")[0], 200)


if __name__ == "__main__":
    unittest.main()
