"""CATAIR FZ (Input) builder and NF / ZD (Output) reader: PDF examples, action rules, response handling."""
import os
import unittest

os.environ.setdefault("FTZ_PBKDF2_ITER", "1000")

from ftz import catair, catair_fz
from ftz.service import ApiError
from tests.test_catair import filing
from tests.test_ftz import Base

IR = "12-345678901"


def act(**kw):
    base = {"qualifier": "2", "id_number": "MAEU123456789", "action": "I", "firms": "A123", "admission_number": "000AAA11120ABC12345"}
    base.update(kw)
    return base


def build(*acts):
    return catair_fz.compile_fz({"actions": list(acts)})


def line80(s):
    return s.ljust(80)


class TestFzBuilder(unittest.TestCase):
    def test_fz10_pdf_example_is_reproduced(self):
        r = build({"qualifier": "1", "id_number": "ABC1234567890ZXY123", "action": "A", "received_qty": 460, "delivery_code": "FI", "irs": "123-45-6789", "firms": "A123"})
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual(r["records"][0]["text"], line80("101ABC1234567890ZXY123                A 0000000460FI123-45-6789 A123 "))
        self.assertTrue(all(len(x["text"]) == 80 for x in r["records"]))

    def test_fz12_pdf_example_is_reproduced(self):
        r = build({"qualifier": "2", "id_number": "MAEU123456789", "action": "K", "pid": "0000000001234567890", "admission_number": "000AAA11120ABC12345"})
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual([x["id"] for x in r["records"]], ["FZ10", "FZ11", "FZ12"])
        self.assertEqual(r["records"][2]["text"], line80("12PID0000000001234567890"))

    def test_every_layout_is_contiguous_and_80_wide(self):
        for rid, spec in list(catair_fz.FZ_LAYOUT.items()) + [(k, {"fields": v}) for k, v in catair_fz.NF_LAYOUT.items()]:
            pos = 1
            for f in spec["fields"]:
                self.assertEqual(f["start"], pos, f"{rid} {f['label']}")
                pos = f["end"] + 1
            self.assertEqual(pos, 81, rid)

    def test_action_rules(self):
        self.assertTrue(any("not used" in e for e in build(act(action="E"))["errors"]))
        self.assertTrue(any("Qualifier" in e for e in build(act(action="H", qualifier="2"))["errors"]))      # H is admission level only
        self.assertTrue(any("FI" in e for e in build(act(qualifier="1", action="A", received_qty=5, delivery_code="PA"))["errors"]))
        self.assertTrue(any("Delivery Code" in e for e in build(act(action="B", received_qty=5))["errors"]))
        ptt = build(act(action="F", received_qty=10, firms=""))
        self.assertTrue(any("IRS Identifier" in e for e in ptt["errors"]) and any("FIRMS" in e for e in ptt["errors"]))
        self.assertTrue(build(act(action="F", received_qty=10, irs=IR, firms="A123"))["ok"])
        self.assertTrue(any("PID" in e for e in build(act(action="K"))["errors"]))
        self.assertTrue(build(act(qualifier="1", action="H", id_number="000AAA11120ABC12345"))["ok"])

    def test_container_ptt_needs_a_container_and_quantity(self):
        self.assertTrue(any("container" in e for e in build(act(qualifier="4", action="F", received_qty=1, irs=IR, firms="A123"))["errors"]))
        r = build(act(qualifier="4", action="F", irs=IR, firms="A123", containers=[{"container": "demu1234567", "qty": 5}, {"container": "DEMU7654321", "qty": 6}]))
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual([x["id"] for x in r["records"]], ["FZ10", "FZ11", "FZ11"])
        self.assertTrue(r["records"][1]["text"].startswith("11DEMU1234567"))
        self.assertTrue(r["records"][1]["text"].rstrip().endswith("000000000000005"))
        self.assertIn("0000000011", r["records"][0]["text"])                    # FZ10 quantity = 5 + 6

    def test_air_needs_flight_details_and_airport(self):
        r = build(act(action="I", air=True, qualifier="2"))
        for needle in ("Airport Code", "Airline Carrier Code", "Flight Number", "Scheduled Date"):
            self.assertTrue(any(needle in e for e in r["errors"]), needle)
        ok = build(act(action="I", air=True, airport="dca", carrier_code="aa", flight="100", sched_arrival="2026-10-20"))
        self.assertTrue(ok["ok"], ok["errors"])
        self.assertIn("20261020", ok["records"][1]["text"])

    def test_direct_delivery_does_not_need_an_admission_number(self):
        r = build(act(action="I", admission_number="", direct_delivery=True))
        self.assertTrue(r["ok"], r["errors"])
        self.assertFalse(build(act(action="I", admission_number=""))["ok"])

    def test_post_admission_correction(self):
        pc = {"contact_name": "Jo Smith", "contact_phone": "(555) 123-4567", "reason": "03"}
        bad = build({"qualifier": "1", "id_number": "000AAA11120ABC12345", "action": "J", "correction": pc})
        self.assertTrue(any("replacement number" in e for e in bad["errors"]))
        good = build({"qualifier": "1", "id_number": "000AAA11120ABC12345", "action": "J", "correction": dict(pc, ref={"qualifier": "FTZ", "ref_id": "000AAA11120ABC99999"})})
        self.assertTrue(good["ok"], good["errors"])
        self.assertEqual([x["id"] for x in good["records"]], ["FZ10", "FZ13", "FZ14"])
        self.assertIn("5551234567", good["records"][1]["text"])
        clerical = build({"qualifier": "1", "id_number": "000AAA11120ABC12345", "action": "J", "correction": dict(pc, reason="01")})
        self.assertTrue(clerical["ok"], clerical["errors"])
        self.assertTrue(any("01 to 11" in e for e in build({"qualifier": "1", "id_number": "000AAA11120ABC12345", "action": "J", "correction": dict(pc, reason="12")})["errors"]))

    def test_remarks_limit_and_order(self):
        r = build(act(remarks=["one"] * 10))
        self.assertTrue(r["ok"], r["errors"])
        self.assertEqual(r["counts"]["FZ20"], 10)
        self.assertFalse(build(act(remarks=["x"] * 11))["ok"])

    def test_empty_and_garbage_never_raise(self):
        self.assertFalse(catair_fz.compile_fz({})["ok"])
        self.assertFalse(catair_fz.compile_fz(None)["ok"])
        self.assertFalse(catair_fz.compile_fz({"actions": [None, "x", {}]})["ok"])

    def test_fz_envelope_uses_the_fz_application_code(self):
        head, tail, errs = catair.build_envelope({"site_code": "5301", "sender_id": "ABC", "filer_code": "ZZZ", "port_code": "5301"}, "abcdef", app="FZ")
        self.assertEqual(errs, [])
        self.assertEqual(head[0][25:27], "FZ")
        self.assertEqual(head[1][10:12], "FZ")
        ft, _, _ = catair.build_envelope({"site_code": "5301", "sender_id": "ABC", "filer_code": "ZZZ", "port_code": "5301"}, "abcdef")
        self.assertEqual(ft[0][25:27], "FT")


def nf(disp="B1", typ="  ", code="   ", text="", adm=("999AB01", "26", "0001"), ref=("1", "999AB01260001")):
    zone, yy, ctl = adm
    n90 = "90" + " " + zone.ljust(9) + yy + ctl.ljust(8) + "5301" + "N"
    n91 = "91" + disp.ljust(3) + ref[0].ljust(3) + ref[1].ljust(35) + "261009" + "0930" + "    " + "D999" + " " * 19
    n95 = "95" + typ + code + text.ljust(60)
    return "\n".join(x.ljust(80) for x in (n90, n91, n95))


class TestResponses(unittest.TestCase):
    def test_pdf_example_nf91_and_95(self):
        out = catair_fz.parse_output("91JDE1  01234567820ABC12345                2002280900    A123                    \n95     OFFICER REMARKS IF APPLICABLE                                             ")
        self.assertEqual(out["problems"], [])
        self.assertEqual([r["id"] for r in out["records"]], ["NF91", "NF95"])
        res = out["results"][0]
        self.assertEqual((res["disposition"], res["outcome"], res["refs"][0]["date"], res["refs"][0]["qualifier"]), ("JDE", "rejected", "2020-02-28", "FTZ admission number"))
        self.assertEqual(res["refs"][0]["id"], "01234567820ABC12345")
        self.assertEqual(res["messages"][0]["text"], "OFFICER REMARKS IF APPLICABLE")

    def test_accepted_rejected_and_warning_responses(self):
        a = catair_fz.parse_output(nf("B1", "  ", "083", "ZONE ADMISSION DATA ACCEPTED"))["results"][0]
        self.assertEqual((a["outcome"], a["admission_number"]), ("accepted", "999AB01260001"))
        self.assertIn("accepted", a["summary"].lower())
        r = catair_fz.parse_output(nf("B3", "01", "082", "ZONE ADMISSION DATA REJECTED"))["results"][0]
        self.assertEqual(r["outcome"], "rejected")
        w = catair_fz.parse_output(nf("B2", "03", "   ", "DATA ACCEPTED WITH WARNINGS"))["results"][0]
        self.assertEqual(w["outcome"], "warning")
        au = catair_fz.parse_output(nf("BF"))["results"][0]
        self.assertEqual(au["outcome"], "authorized")
        fz = catair_fz.parse_output(nf("B7", "01", "120", "TRANSACTION DATA REJECTED"))["results"][0]
        self.assertEqual(fz["outcome"], "rejected")

    def test_decoded_fields_and_dates(self):
        res = catair_fz.parse_output(nf("B1", "02", "116", "PTT MESSAGE DATA ACCEPTED", ref=("2", "MAEU123456789")))
        ref = res["results"][0]["refs"][0]
        self.assertEqual((ref["qualifier"], ref["id"], ref["date"], ref["time"], ref["firms"]), ("Bill of lading", "MAEU123456789", "2026-10-09", "09:30", "D999"))
        self.assertEqual(res["results"][0]["messages"][0]["type_text"], "Data acceptance")
        self.assertEqual(res["results"][0]["messages"][0]["code"], "116")

    def test_trimmed_lines_are_padded_and_wrong_lengths_reported(self):
        trimmed = "\n".join(x.rstrip() for x in nf("B1", "  ", "083", "OK").split("\n"))
        self.assertEqual(catair_fz.parse_output(trimmed)["results"][0]["outcome"], "accepted")
        bad = catair_fz.parse_output(nf() + "\n" + "9" * 90)
        self.assertTrue(any("exactly 80" in p for p in bad["problems"]))

    def test_rejection_echoes_the_line_in_error(self):
        text = nf("B3", "01", "082", "REJECTED").split("\n")
        echoed = "50000001851822000000    CN000000050000NO".ljust(80)
        res = catair_fz.parse_output("\n".join(text[:2] + [echoed, text[2]]))
        self.assertEqual(len(res["results"][0]["echoed"]), 1)
        self.assertEqual(res["results"][0]["outcome"], "rejected")

    def test_zd_copy_is_decoded_with_the_zd_layout(self):
        r = catair.compile_filing(filing())
        out = catair_fz.parse_output("\n".join(x["text"] for x in r["records"]), "zd")
        self.assertEqual(out["kind"], "zd")
        self.assertEqual(out["records"][0]["id"], "ZD10")
        self.assertEqual(next(f["value"] for f in out["records"][0]["fields"] if f["label"] == "Zone ID"), "000AAA111")
        self.assertEqual(out["problems"], [])

    def test_huge_or_binary_input_does_not_crash(self):
        catair_fz.parse_output("\x00" * 500)
        catair_fz.parse_output("9" * 100000)
        catair_fz.parse_output("")


class TestFzService(Base):
    def setUp(self):
        super().setUp()
        self.s.set_settings({"abi_site_code": "5301", "abi_sender_id": "abc", "abi_filer_code": "zzz", "abi_port_code": "5301"})

    def test_fz_filing_save_export_uses_fz_envelope_and_name(self):
        f = self.s.ft_save({"kind": "FZ", "label": "Concur", "data": {"actions": [{"qualifier": "1", "id_number": "000AAA11120ABC12345", "action": "H"}]}})
        self.assertEqual((f["kind"], f["status"], f["action"]), ("FZ", "ready", "H"))
        out = self.s.ft_export(f["id"], {"mode": "batch", "password": "abcdef"})
        self.assertTrue(out["filename"].startswith("FZ-"))
        lines = out["text"].splitlines()
        self.assertEqual((lines[0][25:27], lines[1][10:12]), ("FZ", "FZ"))
        self.assertEqual(lines[2][:2], "10")
        self.assertEqual(self.s.ft_get(f["id"])["status"], "exported")

    def test_kind_is_remembered_on_update_and_validated(self):
        f = self.s.ft_save({"kind": "FZ", "data": {"actions": []}})
        g = self.s.ft_save({"id": f["id"], "data": {"actions": [{"qualifier": "1", "id_number": "000AAA11120ABC12345", "action": "G"}]}})
        self.assertEqual((g["kind"], g["status"]), ("FZ", "ready"))
        for bad in ({"kind": "XX", "data": {}}, {"kind": "FZ", "data": {"actions": "x"}}, {"kind": "FZ", "data": {"actions": ["x"]}}):
            with self.assertRaises(ApiError):
                self.s.ft_save(bad)

    def test_response_updates_matching_filings_and_is_logged(self):
        ft = self.s.ft_save({"data": filing()})
        number = ft["admission_number"]
        z, yy, ctl = number[:7], number[7:9], number[9:]
        text = nf("B3", "01", "082", "ZONE ADMISSION DATA REJECTED", adm=(z, yy, ctl), ref=("1", number))
        out = self.s.ft_response({"text": text})
        self.assertEqual((out["matched"], out["results"][0]["outcome"]), (1, "rejected"))
        self.assertEqual(self.s.ft_get(ft["id"])["status"], "rejected")
        self.s.ft_response({"text": nf("B1", "  ", "083", "ACCEPTED", adm=(z, yy, ctl), ref=("1", number))})
        self.assertEqual(self.s.ft_get(ft["id"])["status"], "accepted")
        self.s.ft_response({"text": nf("BF", adm=(z, yy, ctl), ref=("1", number))})
        self.assertEqual(self.s.ft_get(ft["id"])["status"], "authorized")
        self.s.ft_response({"text": nf("B1", "  ", "083", "ACCEPTED", adm=(z, yy, ctl), ref=("1", number))})
        self.assertEqual(self.s.ft_get(ft["id"])["status"], "authorized")        # a repeat does not downgrade
        self.assertEqual(len(self.s.ft_responses(ft["id"])), 4)
        self.assertTrue(any(a["action"] == "response" for a in self.s.audit_log()))

    def test_response_to_an_fz_filing_matches_by_bill_of_lading(self):
        f = self.s.ft_save({"kind": "FZ", "data": {"actions": [act(action="I")]}})
        out = self.s.ft_response({"text": nf("B6", "02", "119", "PHYSICAL ARRIVAL DATA ACCEPTED", adm=("", "00", ""), ref=("2", "MAEU123456789"))})
        self.assertEqual(out["matched"], 1)
        self.assertEqual(self.s.ft_get(f["id"])["status"], "accepted")

    def test_unmatched_response_is_reported_and_zd_is_not_a_response(self):
        out = self.s.ft_response({"text": nf("B1", "  ", "083", "OK")})
        self.assertEqual(out["matched"], 0)
        with self.assertRaises(ApiError):
            self.s.ft_response({"text": "", })
        with self.assertRaises(ApiError):
            self.s.ft_response({"text": "garbage"})
        dec = self.s.ft_decode({"text": nf("B1", "  ", "083", "OK")})
        self.assertEqual(dec["kind"], "nf")

    def test_pinned_filing(self):
        f = self.s.ft_save({"data": filing()})
        out = self.s.ft_response({"text": nf("B1", "  ", "083", "OK"), "filing_id": f["id"]})
        self.assertEqual(out["matched"], 1)
        with self.assertRaises(ApiError):
            self.s.ft_response({"text": nf(), "filing_id": 99999})


if __name__ == "__main__":
    unittest.main()
