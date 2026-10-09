"""FT filings through the service and HTTP layers: storage, prefill from an e214, export, permissions, no password leakage."""
import json
import os
import unittest

os.environ.setdefault("FTZ_PBKDF2_ITER", "1000")

from ftz import auth, catair
from ftz.service import ApiError
from tests.test_catair import bill, conv, filing, header, line
from tests.test_ftz import Base, Site, TODAY


class TestFtService(Base):
    def setUp(self):
        super().setUp()
        self.s.set_settings({"abi_site_code": "5301", "abi_sender_id": "abc", "abi_filer_code": "zzz", "abi_port_code": "5301"})

    def test_save_check_status_and_listing(self):
        f = self.s.ft_save({"data": filing(), "label": "Example"})
        self.assertEqual((f["status"], f["action"], f["admission_number"]), ("ready", "A", "000AAA11120ABC12345"))
        bad = self.s.ft_save({"id": f["id"], "data": filing(header=header(zone_id="12"))})
        self.assertEqual(bad["status"], "draft")
        self.assertEqual(len(self.s.ft_list()), 1)
        self.assertTrue(self.s.ft_get(f["id"])["check"]["errors"])
        self.assertTrue(any(a["entity"] == "ft_filing" for a in self.s.audit_log()))

    def test_bad_shapes_are_rejected(self):
        for bad in ({"data": []}, {"data": {"conveyances": "x"}}, {"data": {"conveyances": [{"bills": [{"lines": ["x"]}]}]}}):
            with self.assertRaises(ApiError):
                self.s.ft_save(bad)

    def test_prefill_from_an_admission_fills_what_it_knows_and_lists_the_rest(self):
        self.s.upsert_part({"part_no": "SPK-1", "description": "Speakers, Bluetooth", "htsus": "8518.22.0000", "coo": "cn", "uom": "PCS", "mid": "cnabc123"})
        z = self.s.create_zone({"zone_no": "FTZ 123", "name": "Z", "port_code": "5301", "firms": "d123"})
        self.s.create_party({"kind": "operator", "name": "Op2", "ident": "EIN 12-345678901"})
        adm = self.s.save_admission({"zone_id": z["id"], "operator_id": self.s.list_admissions()[0]["operator_id"] if self.s.list_admissions() else self.op,
                                     "carrier_id": self.car, "transport_doc": "bl 12345", "entry_date": "2026-10-03", "vessel_voyage": "v.12",
                                     "lines": [{"part_no": "SPK-1", "qty": 100, "value": 1000.6, "uom": "PCS", "zone_status": "PF", "duty_rate": 4.9}]})
        out = self.s.ft_from_admission(adm["id"])
        h, c = out["data"]["header"], out["data"]["conveyances"][0]
        self.assertEqual((h["zone_id"], h["calendar_year"], h["port_code"], h["firms"], h["abi_filer"]), ("123", "26", "5301", "D123", "ZZZ"))
        ln = c["bills"][0]["lines"][0]
        self.assertEqual((c["bills"][0]["bill"], c["voyage"], ln["htsus"], ln["coo"], ln["zone_status"], ln["value"]), ("BL12345", "V 12", "8518220000", "CN", "P", 1001))
        self.assertEqual(ln["refs"][0]["ref_id"], "CNABC123")
        self.assertEqual(ln["refs"][0]["description"], "SPEAKERS BLUETOOTH")
        notes = " | ".join(out["notes"])
        for needle in ("Zone ID", "Mode of Transportation", "Weight and transportation charges", "simplified", "rounded"):
            self.assertIn(needle, notes)
        self.assertEqual(out["status"], "draft")                      # honest: it still needs a person's input
        self.assertFalse(out["check"]["ok"])

    def test_prefill_does_not_invent_a_mid(self):
        adm = self.s.save_admission({"zone_id": self.zone, "carrier_id": self.car, "transport_doc": "BL1BL1", "entry_date": TODAY,
                                     "lines": [{"description": "Widgets", "htsus": "8471.30.0100", "coo": "CN", "qty": 5, "uom": "PCS", "value": 50, "zone_status": "D"}]})
        out = self.s.ft_from_admission(adm["id"])
        self.assertEqual(out["data"]["conveyances"][0]["bills"][0]["lines"][0]["refs"][0]["ref_id"], "")
        self.assertTrue(any("MID" in n for n in out["notes"]))

    def export_ready(self, **kw):
        f = self.s.ft_save({"data": filing(**kw)})
        self.assertEqual(f["status"], "ready", f["check"]["errors"])
        return f

    def test_export_batch_and_body(self):
        f = self.export_ready()
        out = self.s.ft_export(f["id"], {"mode": "batch", "password": "pw1234", "eol": "crlf"})
        lines = out["text"].split("\r\n")
        self.assertEqual((lines[0][0], lines[1][0], lines[-3][0], lines[-2][0]), ("A", "B", "Y", "Z"))
        self.assertEqual(lines[0][8:14], "PW1234")
        self.assertTrue(out["filename"].startswith("FT-000AAA11120ABC12345-A"))
        body = self.s.ft_export(f["id"], {"mode": "body"})["text"].split("\n")
        self.assertEqual((body[0][:2], len(body)), ("10", len(lines) - 4))
        self.assertEqual(self.s.ft_get(f["id"])["status"], "exported")
        self.assertEqual(len(self.s.ft_exports(f["id"])), 2)

    def test_the_abi_password_is_never_stored_or_audited(self):
        f = self.export_ready()
        self.s.ft_export(f["id"], {"mode": "batch", "password": "Sup3rS"})
        dump = json.dumps([dict(r) for r in self.con.execute("SELECT * FROM ft_exports")] + [dict(r) for r in self.con.execute("SELECT * FROM audit")]
                          + [dict(r) for r in self.con.execute("SELECT * FROM settings")]).upper()
        self.assertNotIn("SUP3RS", dump)

    def test_export_is_refused_for_invalid_filings_missing_password_or_envelope(self):
        bad = self.s.ft_save({"data": filing(header=header(zone_id="1"))})
        with self.assertRaises(ApiError) as c:
            self.s.ft_export(bad["id"], {"mode": "body"})
        self.assertIn("Fix these before exporting", str(c.exception))
        good = self.export_ready()
        with self.assertRaises(ApiError) as c:
            self.s.ft_export(good["id"], {"mode": "batch"})
        self.assertIn("password", str(c.exception))
        self.s.set_settings({"abi_site_code": ""})
        with self.assertRaises(ApiError) as c:
            self.s.ft_export(good["id"], {"mode": "batch", "password": "pw1234"})
        self.assertIn("Site Code", str(c.exception))
        with self.assertRaises(ApiError):
            self.s.ft_export(good["id"], {"mode": "weird"})

    def test_exported_filings_cannot_be_deleted_but_drafts_can(self):
        f = self.export_ready()
        self.s.ft_export(f["id"], {"mode": "body"})
        with self.assertRaises(ApiError):
            self.s.ft_delete(f["id"])
        d = self.s.ft_save({"data": filing()})
        self.s.ft_delete(d["id"])
        self.assertEqual(len(self.s.ft_list()), 1)

    def test_settings_validation_for_the_envelope(self):
        self.assertEqual(self.s.settings()["abi_site_code"], "5301")
        for k, v in (("abi_site_code", "53"), ("abi_sender_id", "ABCD"), ("abi_filer_code", "a!c")):
            with self.assertRaises(ApiError):
                self.s.set_settings({k: v})
        self.s.set_settings({"abi_office_code": ""})

    def test_master_data_for_mid_and_firms(self):
        with self.assertRaises(ApiError):
            self.s.upsert_part({"part_no": "M1", "mid": "bad mid!"})
        self.assertEqual(self.s.upsert_part({"part_no": "M1", "mid": "cnabc1"})["mid"], "CNABC1")
        self.assertEqual(self.s.create_zone({"zone_no": "FTZ 5", "name": "N", "firms": "ab12"})["firms"], "AB12")


class TestFtHttp(unittest.TestCase):
    def setUp(self):
        auth.LIMITS.h.clear()
        self.site = Site(FTZ_AUTH_USER="owner", FTZ_AUTH_PASSWORD="owner-pass-12345", FTZ_DEMO_MODE="1")
        self.site.login("owner", "owner-pass-12345")

    def tearDown(self):
        self.site.close()

    def j(self, method, path, body=None):
        st, data, _ = self.site.call(method, path, body)
        return st, json.loads(data) if data and data[:1] in b"[{" else data

    def test_spec_check_save_export_flow(self):
        st, spec = self.j("GET", "/api/ft/spec")
        self.assertEqual(st, 200)
        self.assertEqual(spec["source"]["ft"]["version"], "3.1.3")
        self.assertEqual(set(spec["layout"]), {"FT10", "FT11", "FT12", "FT20", "FT40", "FT41", "FT42", "FT43", "FT50", "FT51", "FT60", "FT61"})
        self.assertEqual(spec["maps"]["main"]["rows"][1][0], "FT10")
        st, chk = self.j("POST", "/api/ft/check", {"data": filing()})
        self.assertEqual((st, chk["ok"], chk["counts"]["FT50"]), (200, True, 1))
        st, saved = self.j("POST", "/api/ft", {"data": filing(), "label": "x"})
        self.assertEqual((st, saved["status"]), (200, "ready"))
        self.j("POST", "/api/settings".replace("/api/settings", "/api/ft/check"), {"data": {}})
        self.assertEqual(self.site.call("PUT", "/api/settings", {"abi_site_code": "5301", "abi_sender_id": "ABC", "abi_filer_code": "ZZZ", "abi_port_code": "5301"})[0], 200)
        st, out = self.j("POST", f"/api/ft/{saved['id']}/export", {"mode": "batch", "password": "pw1234"})
        self.assertEqual(st, 200)
        self.assertTrue(all(len(x) == 80 for x in out["text"].splitlines()))
        self.assertEqual(self.j("POST", f"/api/ft/{saved['id']}/export", {"mode": "batch"})[0], 400)       # password required

    def test_demo_sample_filing_exists_and_is_valid(self):
        st, lst = self.j("GET", "/api/ft")
        self.assertTrue(lst, "the demo should ship a sample FT filing")
        st, f = self.j("GET", f"/api/ft/{lst[0]['id']}")
        self.assertTrue(f["check"]["ok"], f["check"]["errors"])

    def test_from_admission_and_permissions(self):
        st, adm = self.j("GET", "/api/admissions")
        st, f = self.j("POST", f"/api/ft/from-admission/{adm[0]['id']}")
        self.assertEqual(st, 200)
        self.assertTrue(f["notes"])
        # integration tokens must not reach filings; anonymous visitors neither
        tok = self.j("POST", "/api/tokens", {"name": "feed"})[1]["token"]
        anon = Site.__new__(Site)
        anon.__dict__.update(self.site.__dict__)
        anon.cookie = None
        self.assertEqual(anon.call("GET", "/api/ft", headers={"Authorization": f"Bearer {tok}"})[0], 403)
        self.assertEqual(anon.call("GET", "/api/ft")[0], 401)
        self.assertEqual(anon.call("POST", "/api/ft/check", {"data": filing()})[0], 401)


if __name__ == "__main__":
    unittest.main()
