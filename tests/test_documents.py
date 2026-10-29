"""Stored documents: validation, integrity, retention (no deletes), permissions, both back ends."""
import base64
import hashlib
import json
import os
import unittest

os.environ.setdefault("FTZ_PBKDF2_ITER", "1000")

from ftz import db
from ftz.service import ApiError
from tests import test_hardening as hard
from tests.test_ftz import Base

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def b64(b):
    return base64.b64encode(b).decode()


class TestDocuments(Base):
    def up(self, **kw):
        d = dict(entity="admission", entity_id=self.s.list_admissions()[0]["id"] if self.s.list_admissions() else 0, filename="invoice 001.pdf", data=b64(PDF), kind="invoice")
        d.update(kw)
        return self.s.doc_upload(d)

    def setUp(self):
        super().setUp()
        self.s.save_admission({"zone_id": self.zone, "carrier_id": self.car, "transport_doc": "BL1BL1", "entry_date": "2026-10-01",
                               "lines": [{"description": "Widgets", "htsus": "8471.30.0100", "coo": "CN", "qty": 5, "uom": "PCS", "value": 50, "zone_status": "NPF"}]})

    def test_store_list_download_round_trip(self):
        d = self.up(note="commercial invoice")
        self.assertEqual((d["filename"], d["content_type"], d["size"], d["sha256"]), ("invoice 001.pdf", "application/pdf", len(PDF), hashlib.sha256(PDF).hexdigest()))
        self.assertEqual(len(self.s.doc_list("admission", d["entity_id"])), 1)
        self.assertNotIn("content", self.s.doc_list()[0])                  # listings never carry the file itself
        name, ctype, data = self.s.doc_download(d["id"])
        self.assertEqual((name, ctype, data), ("invoice 001.pdf", "application/pdf", PDF))
        self.assertEqual([a["action"] for a in self.s.audit_log() if a["entity"] == "document"][:2], ["download", "upload"])

    def test_the_same_file_twice_is_not_stored_twice(self):
        first = self.up()
        again = self.up()
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(len(self.s.doc_list()), 1)

    def test_validation(self):
        for bad, why in ((dict(filename="evil.exe"), "type"), (dict(filename="noext"), "name"), (dict(data=b64(b"not a pdf")), "pdf bytes"),
                         (dict(data=""), "empty"), (dict(data="%%%"), "base64"), (dict(entity="nope"), "entity"), (dict(entity_id=9999), "record"),
                         (dict(kind="weird"), "kind"), (dict(filename="a.csv", data=b64(b"\x00\x01")), "binary csv"), (dict(filename="a.png", data=b64(PDF)), "mismatch")):
            with self.assertRaises(ApiError, msg=why):
                self.up(**bad)

    def test_size_limit(self):
        from ftz import documents
        with self.assertRaises(ApiError):
            self.up(data=b64(PDF + b"x" * documents.MAX_BYTES))

    def test_file_names_are_cleaned(self):
        d = self.up(filename="..\\..\\etc/pass<wd>.pdf")
        self.assertEqual(d["filename"], "pass_wd_.pdf")

    def test_documents_cannot_be_deleted_or_edited_even_in_sql(self):
        d = self.up()
        for sql in ("DELETE FROM documents", "UPDATE documents SET sha256='x'", "UPDATE documents SET filename='y.pdf'"):
            self.con.execute("SAVEPOINT g")
            with self.assertRaises(db.INTEGRITY_ERRORS, msg=sql):
                self.con.execute(sql)
            self.con.execute("ROLLBACK TO g")
        self.assertEqual(len(self.s.doc_list()), 1)

    def test_void_keeps_the_record_and_needs_a_reason(self):
        d = self.up()
        with self.assertRaises(ApiError):
            self.s.doc_void(d["id"], {"reason": ""})
        v = self.s.doc_void(d["id"], {"reason": "wrong invoice attached"})
        self.assertEqual(v["void_reason"], "wrong invoice attached")
        self.assertEqual(self.s.doc_list("admission", d["entity_id"], include_voided=False), [])
        self.assertEqual(len(self.s.doc_list("admission", d["entity_id"])), 1)
        self.assertEqual(self.s.doc_download(d["id"])[2], PDF)                  # still on record
        with self.assertRaises(ApiError):
            self.s.doc_void(d["id"], {"reason": "again"})
        self.con.execute("SAVEPOINT g")
        with self.assertRaises(db.INTEGRITY_ERRORS):
            self.con.execute("UPDATE documents SET void_reason='changed' WHERE id=?", (d["id"],))
        self.con.execute("ROLLBACK TO g")

    def test_tampered_content_is_refused_and_reported(self):
        d = self.up()
        self.assertTrue(self.s.verify_integrity()["documents"]["ok"])
        if db.use_pg():
            self.con.execute("ALTER TABLE documents DISABLE TRIGGER USER")
        else:
            self.con.execute("DROP TRIGGER documents_no_edit")
        self.con.execute("UPDATE documents SET content=? WHERE id=?", (PDF + b" tampered", d["id"]))
        res = self.s.verify_integrity()
        self.assertFalse(res["ok"])
        self.assertEqual(res["documents"]["mismatches"][0]["id"], d["id"])
        with self.assertRaises(ApiError) as cm:
            self.s.doc_download(d["id"])
        self.assertEqual(cm.exception.status, 500)

    def test_general_documents_and_search(self):
        self.s.doc_upload(dict(entity="general", filename="bond.pdf", data=b64(PDF), kind="bond", note="continuous bond rider"))
        self.assertEqual(len(self.s.doc_list(q="rider")), 1)
        self.assertEqual(self.s.doc_list(q="zzz"), [])


class TestDocumentsHttp(hard.SiteCase):
    def test_upload_download_headers_and_roles(self):
        s = self.start()
        s.login("owner", "owner-pass-12345")
        s.call("POST", "/api/zones", {"zone_no": "FTZ 1", "name": "Z"})
        s.call("POST", "/api/users", {"username": "auditor1", "password": "Audit-pass-2026!", "role": "auditor"})
        st, body, _ = s.call("POST", "/api/documents", {"entity": "general", "filename": "Bond Rider.pdf", "data": b64(PDF), "kind": "bond"})
        self.assertEqual(st, 200, body)
        doc = json.loads(body)
        st, data, r = s.call("GET", f"/api/documents/{doc['id']}/download")
        self.assertEqual((st, data), (200, PDF))
        self.assertIn("attachment", r.getheader("Content-Disposition"))
        self.assertEqual(r.getheader("Content-Type"), "application/pdf")
        self.assertIn("sandbox", r.getheader("Content-Security-Policy"))
        self.assertEqual(s.call("GET", "/api/documents/9999/download")[0], 404)
        s.cookie = None
        self.assertEqual(s.call("GET", f"/api/documents/{doc['id']}/download")[0], 401)       # signed-out users get nothing
        self.assertEqual(s.call("GET", "/docs.js")[0], 401)
        s.login("auditor1", "Audit-pass-2026!")
        self.assertEqual(s.call("GET", f"/api/documents/{doc['id']}/download")[0], 200)       # auditors can read
        self.assertEqual(s.call("GET", "/api/documents")[0], 200)
        self.assertEqual(s.call("POST", "/api/documents", {"entity": "general", "filename": "x.pdf", "data": b64(PDF)})[0], 403)
        self.assertEqual(s.call("POST", f"/api/documents/{doc['id']}/void", {"reason": "nope"})[0], 403)

    def test_oversized_request_is_refused(self):
        s = self.start()
        s.login("owner", "owner-pass-12345")
        st, _, _ = s.call("POST", "/api/documents", raw=b"{}", headers={"Content-Length": "13000000"})     # declares 13 MB; refused before reading it
        self.assertEqual(st, 413)
        st, _, _ = s.call("POST", "/api/zones", raw=b"{}", headers={"Content-Length": "6000000"})          # other routes keep the 5 MB cap
        self.assertEqual(st, 413)


if __name__ == "__main__":
    unittest.main()
