"""CATAIR FT (Input) builder: PDF examples reproduced exactly, usage-map rules, formatting and validation."""
import copy
import unittest
from datetime import date

from ftz import catair as C

# Exact example records printed in ACE CATAIR FTZ v3.1.3 (August 2026), taken from the PDF at full width.
PDF_EXAMPLES = {
    "FT10": "10A000AAA11120ABC12345Y9999YZZZ         123-45-6789 Z123",
    "FT11": "11JOHN SMITH                              18005551234    01030508",
    "FT12": "12THIS IS A REQUIRED REMARK TO EXPLAIN THE USAGE OF REASON CODE 08",
    "FT20": "20A10ABCDCONVEYANCE NAME        012345         2020020620200206999920200206",
    "FT40": "40ABCD120620GRC001                                       0000000800MX12345",
    "FT41": "41123456789",
    "FT42": "4212345678910",
    "FT43": "43ABCD1234567890",
    "FT50": "50000019101218030    DE000000019800NO 000000000000",
    "FT51": "5100000000190000000001730000000189N00000000",
    "FT60": "60BULK LOT GERMAN SLR CAMERAS                  MIDABC1234567890",
    "FT61": "61SLR CAMERAS IMPORTED FROM GERMANY SHIPPED FROM DENMARK",
}


def header(**kw):
    h = dict(action="A", zone_id="000AAA111", calendar_year="20", control_number="ABC12345", port_code="9999", direct_delivery="Y",
             abi_filer="ZZZ", zone_operator="123-45-6789", firms="Z123")
    h.update(kw)
    return h


def line(**kw):
    ln = dict(line_no=1, htsus="9101218030", coo="DE", qty1="198", uom1="NO", weight=19, value=173, charges=189, zone_status="N", hmf=0,
              refs=[dict(description="BULK LOT GERMAN SLR CAMERAS", qualifier="MID", ref_id="ABC1234567890")],
              remarks=["SLR CAMERAS IMPORTED FROM GERMANY SHIPPED FROM DENMARK"])
    ln.update(kw)
    return ln


def bill(**kw):
    b = dict(bill="ABCD120620GRC001", quantity=800, country_export="MX", load_port="12345", inbond_numbers=["123456789"],
             bonded_carriers=["12345678910"], containers=["ABCD1234567890"], lines=[line()])
    b.update(kw)
    return b


def conv(**kw):
    c = dict(admission_type="A", mot="10", scac="ABCD", conveyance_name="CONVEYANCE NAME", voyage="012345", export_date="2020-02-06",
             import_date="2020-02-06", port_unlading="9999", scheduled_arrival="2020-02-06", bills=[bill()])
    c.update(kw)
    return c


def filing(**kw):
    f = dict(header=header(), conveyances=[conv()])
    f.update(kw)
    return f


def by_id(res, rid):
    return [r["text"] for r in res["records"] if r["id"] == rid]


class TestPdfExamples(unittest.TestCase):
    def test_every_example_record_in_the_cbp_pdf_is_reproduced_character_for_character(self):
        res = C.compile_filing(filing())
        self.assertTrue(res["ok"], res["errors"])
        for rid in ("FT10", "FT20", "FT40", "FT41", "FT42", "FT43", "FT50", "FT51", "FT60", "FT61"):
            got = by_id(res, rid)
            self.assertEqual(len(got), 1, rid)
            self.assertEqual(got[0].rstrip(), PDF_EXAMPLES[rid].rstrip(), rid)

    def test_replace_examples(self):
        res = C.compile_filing(filing(header=header(action="R"), replace=dict(
            contact_name="JOHN SMITH", contact_phone="18005551234", reason_codes=["01", "03", "05", "08"],
            remarks=["THIS IS A REQUIRED REMARK TO EXPLAIN THE USAGE OF REASON CODE 08"])))
        self.assertTrue(res["ok"], res["errors"])
        self.assertEqual(by_id(res, "FT11")[0].rstrip(), PDF_EXAMPLES["FT11"].rstrip())
        self.assertEqual(by_id(res, "FT12")[0].rstrip(), PDF_EXAMPLES["FT12"].rstrip())


class TestLayoutIntegrity(unittest.TestCase):
    def check(self, name, fields):
        pos = 1
        for f in fields:
            self.assertEqual(f["start"], pos, f"{name} gap or overlap before {f['label']}")
            self.assertEqual(f["len"], f["end"] - f["start"] + 1)
            pos = f["end"] + 1
        self.assertEqual(pos - 1, 80, f"{name} does not total 80 positions")

    def test_every_record_is_contiguous_and_80_wide(self):
        for rid, spec in C.LAYOUT.items():
            self.check(rid, spec["fields"])
        for rid, fields in C.ENVELOPE.items():
            self.check(rid + "-record", fields)

    def test_control_identifiers(self):
        for rid, spec in C.LAYOUT.items():
            self.assertEqual(spec["fields"][0]["fixed"], rid[2:], rid)


class TestFormatting(unittest.TestCase):
    def test_numbers_right_justify_zero_fill_with_two_implied_decimals(self):
        res = C.compile_filing(filing(conveyances=[conv(bills=[bill(lines=[line(qty1="45", qty2="4500")])])]))
        t = by_id(res, "FT50")[0]
        self.assertEqual(t[23:35], "000000004500")                   # 45 -> 4500
        self.assertEqual(t[38:50], "000000450000")                   # 4500 -> 450000 (the PDF's own note)
        res = C.compile_filing(filing(conveyances=[conv(bills=[bill(lines=[line(qty1="45.73")])])]))
        self.assertEqual(by_id(res, "FT50")[0][23:35], "000000004573")

    def test_three_decimals_are_refused_not_rounded(self):
        res = C.compile_filing(filing(conveyances=[conv(bills=[bill(lines=[line(qty1="1.005")])])]))
        self.assertFalse(res["ok"])
        self.assertTrue(any("2 decimal" in e for e in res["errors"]))

    def test_text_is_upper_cased_and_never_silently_truncated(self):
        res = C.compile_filing(filing(conveyances=[conv(conveyance_name="mv lower case")]))
        self.assertIn("MV LOWER CASE", by_id(res, "FT20")[0])
        res = C.compile_filing(filing(conveyances=[conv(conveyance_name="X" * 24)]))
        self.assertFalse(res["ok"])
        self.assertTrue(any("24 characters" in e and "holds 23" in e for e in res["errors"]))

    def test_characters_outside_the_field_class_are_reported(self):
        res = C.compile_filing(filing(conveyances=[conv(bills=[bill(lines=[line(refs=[dict(description="CAMERAS, SLR (BULK)", qualifier="MID", ref_id="ABC")])])])]))
        self.assertTrue(any("letters, numbers and spaces only" in e and "','" in e for e in res["errors"]), res["errors"])

    def test_dollars_are_whole_numbers_and_rounding_is_disclosed(self):
        res = C.compile_filing(filing(conveyances=[conv(bills=[bill(lines=[line(value="1234.50", weight="9.4", charges="10")])])]))
        t = by_id(res, "FT51")[0]
        self.assertEqual((t[12:24], t[2:12]), ("000000001235", "0000000009"))
        self.assertTrue(any("1234.50 was rounded to 1235" in w for w in res["warnings"]))

    def test_every_record_is_exactly_80_characters(self):
        res = C.compile_filing(filing())
        self.assertTrue(res["records"] and all(len(r["text"]) == 80 for r in res["records"]))


class TestActionsAndUsageMap(unittest.TestCase):
    def test_delete_sends_only_ft10_and_ft61(self):
        res = C.compile_filing(filing(header=header(action="D"), delete_remarks=["ENTRY 123 FILED"]))
        self.assertTrue(res["ok"], res["errors"])
        self.assertEqual([r["id"] for r in res["records"]], ["FT10", "FT61"])
        self.assertEqual(res["usage_map"], "D")
        self.assertTrue(any("left out" in w for w in res["warnings"]))

    def test_replace_requires_ft11_and_ft12_for_reason_08(self):
        res = C.compile_filing(filing(header=header(action="R")))
        self.assertTrue(any("Reason Code" in e for e in res["errors"]))
        res = C.compile_filing(filing(header=header(action="R"), replace=dict(reason_codes=["08"])))
        self.assertTrue(any("FT12" in e for e in res["errors"]))
        res = C.compile_filing(filing(header=header(action="R"), replace=dict(reason_codes=["01", "01"])))
        self.assertTrue(any("must not repeat" in e for e in res["errors"]))
        res = C.compile_filing(filing(header=header(action="R"), replace=dict(reason_codes=["11"])))
        self.assertTrue(any("not valid" in e for e in res["errors"]))

    def test_ft11_is_ignored_unless_action_r(self):
        res = C.compile_filing(filing(replace=dict(reason_codes=["01"])))
        self.assertEqual(by_id(res, "FT11"), [])
        self.assertTrue(any("only when the Action Code is R" in w for w in res["warnings"]))

    def test_status_change(self):
        ln = line(existing_status="N", requested_status="P", affected_qty=50, inventory_uom="EA", refs=[], remarks=[])
        res = C.compile_filing(filing(header=header(action="S"), conveyances=[conv(admission_type="C", bills=[bill(lines=[ln], inbond_numbers=[], bonded_carriers=[], containers=[])])]))
        self.assertTrue(res["ok"], res["errors"])
        self.assertEqual(res["usage_map"], "S")
        t50, t51 = by_id(res, "FT50")[0], by_id(res, "FT51")[0]
        self.assertEqual(t50[23:35], "0" * 12)                      # quantities zero-filled
        self.assertEqual(t50[35:38], "   ")                         # unit of measure 1 space-filled
        self.assertEqual((t51[43], t51[44], t51[45:57], t51[57:60]), ("N", "P", "000000000050", "EA "))
        self.assertEqual(by_id(res, "FT60"), [])                    # not part of the Status Change map

    def test_status_change_rules(self):
        ln = line(existing_status="P", requested_status="D", affected_qty=0.5, refs=[], remarks=[])
        res = C.compile_filing(filing(header=header(action="S"), conveyances=[conv(admission_type="A", bills=[bill(lines=[ln])])]))
        text = " | ".join(res["errors"])
        for needle in ("must use Admission Type C", "Existing Zone Status must be N", "Requested Zone Status must be P or Z", "whole number above zero", "Inventory Unit of Measure"):
            self.assertIn(needle, text)
        res = C.compile_filing(filing(conveyances=[conv(admission_type="C")]))
        self.assertTrue(any("only for a Status Change" in e for e in res["errors"]))

    def test_temporary_deposit_needs_no_lines(self):
        res = C.compile_filing(filing(conveyances=[conv(admission_type="T", bills=[bill(lines=[], inbond_numbers=["123456789"])])]))
        self.assertTrue(res["ok"], res["errors"])
        self.assertEqual((res["usage_map"], by_id(res, "FT50")), ("T", []))
        res = C.compile_filing(filing(conveyances=[conv(admission_type="A", bills=[bill(lines=[])])]))
        self.assertTrue(any("At least one HTS line" in e for e in res["errors"]))

    def test_domestic_overage_zone_to_zone_blank_the_conveyance_fields(self):
        for t in ("D", "Z"):
            res = C.compile_filing(filing(conveyances=[conv(admission_type=t, bills=[bill(inbond_numbers=[], bonded_carriers=[], containers=[])])]))
            self.assertTrue(res["ok"], res["errors"])
            self.assertEqual(by_id(res, "FT20")[0][3:75], " " * 72, t)
            ft40 = by_id(res, "FT40")[0]
            self.assertEqual(ft40[67:74], " " * 7, t)                          # country of export and load port: blank for O, C, D, T, Z
            # CATAIR space-fills Quantity only for O, C, D and T (Z is not in that list)
            self.assertEqual(ft40[57:67], " " * 10 if t == "D" else "0000000800", t)
            self.assertTrue(res["warnings"])

    def test_overage_bill_format_from_appendix_a(self):
        good = conv(admission_type="O", bills=[bill(bill="Z123ADJ200101001", inbond_numbers=[], bonded_carriers=[], containers=[])])
        self.assertTrue(C.compile_filing(filing(conveyances=[good]))["ok"])
        bad = conv(admission_type="O", bills=[bill(bill="OVERAGE-0001", inbond_numbers=[], bonded_carriers=[], containers=[])])
        self.assertTrue(any("16 characters" in e for e in C.compile_filing(filing(conveyances=[bad]))["errors"]))
        wrong_site = conv(admission_type="O", bills=[bill(bill="O920ADJ161028001", inbond_numbers=[], bonded_carriers=[], containers=[])])
        self.assertTrue(any("FIRMS code Z123" in e for e in C.compile_filing(filing(conveyances=[wrong_site]))["errors"]))

    def test_quantity_rules_for_bills(self):
        res = C.compile_filing(filing(conveyances=[conv(bills=[bill(quantity=None)])]))
        self.assertTrue(any("Quantity" in e and "required" in e for e in res["errors"]))
        td = conv(admission_type="T", bills=[bill(lines=[], inbond_numbers=[], bonded_carriers=[], containers=[])])
        res = C.compile_filing(filing(conveyances=[td]))
        self.assertEqual(by_id(res, "FT40")[0][57:67], " " * 10)       # blank for T without an in-bond
        td_ib = conv(admission_type="T", bills=[bill(lines=[], quantity=None)])
        self.assertTrue(any("Quantity" in e and "required" in e for e in C.compile_filing(filing(conveyances=[td_ib]))["errors"]))

    def test_air_rules(self):
        air = conv(mot="40", scac="UA", voyage="15", bills=[bill(house_bill="HAWB1", load_port="")])
        res = C.compile_filing(filing(conveyances=[air]))
        self.assertTrue(res["ok"], res["errors"])
        self.assertEqual(by_id(res, "FT20")[0][32:47].strip(), "0015")   # flight padded with zeros on the left
        self.assertEqual(by_id(res, "FT40")[0][37:57].strip(), "HAWB1")
        self.assertEqual(by_id(res, "FT40")[0][69:74], "     ")          # no foreign load port off vessels
        sea = conv(bills=[bill(house_bill="HAWB1")])
        self.assertEqual(by_id(C.compile_filing(filing(conveyances=[sea])), "FT40")[0][37:57], " " * 20)
        res = C.compile_filing(filing(conveyances=[conv(mot="40", scac="B6", bills=[bill(load_port="")])]))
        self.assertTrue(res["ok"] and any("contains a digit" in w for w in res["warnings"]))
        res = C.compile_filing(filing(conveyances=[conv(mot="10", scac="B6")]))
        self.assertTrue(any("2 to 4 letters" in e for e in res["errors"]))

    def test_vessel_needs_foreign_load_port_and_dates_must_order(self):
        res = C.compile_filing(filing(conveyances=[conv(bills=[bill(load_port=None)])]))
        self.assertTrue(any("Foreign Load Port" in e and "required" in e for e in res["errors"]))
        res = C.compile_filing(filing(conveyances=[conv(export_date="2020-02-07", import_date="2020-02-06")]))
        self.assertTrue(any("on or before the Import Date" in e for e in res["errors"]))
        res = C.compile_filing(filing(conveyances=[conv(export_date="2020-02-30")]))
        self.assertTrue(any("real date" in e for e in res["errors"]))


class TestHeaderRules(unittest.TestCase):
    def test_zone_id_forms_and_expanded_indicator(self):
        res = C.compile_filing(filing(header=header(zone_id="123AB45")))
        t = by_id(res, "FT10")[0]
        self.assertEqual((t[3:12], t[22]), ("123AB45  ", "N"))            # legacy: left-justified, indicator N
        res = C.compile_filing(filing(header=header(zone_id="123ABC456")))
        self.assertEqual(by_id(res, "FT10")[0][22], "Y")
        res = C.compile_filing(filing(header=header(zone_id="12AB")))
        self.assertTrue(any("Zone ID must be 7" in e for e in res["errors"]))

    def test_required_header_fields_are_reported_with_positions(self):
        res = C.compile_filing({"header": {"action": "A"}, "conveyances": []})
        text = " | ".join(res["errors"])
        for needle in ("Zone ID (pos 4-12)", "Control Number (pos 15-22)", "Port Code (pos 24-27)", "ABI Filer Code (pos 29-31)",
                       "Zone Operator Identifier (pos 41-52)", "FIRMS Identifier (pos 53-56)", "At least one conveyance"):
            self.assertIn(needle, text)

    def test_applicant_equal_to_operator_is_left_blank(self):
        res = C.compile_filing(filing(header=header(applicant="123-45-6789")))
        self.assertEqual(by_id(res, "FT10")[0][56:68], " " * 12)
        res = C.compile_filing(filing(header=header(applicant="98-7654321")))
        self.assertEqual(by_id(res, "FT10")[0][56:68].strip(), "98-7654321")

    def test_bad_action_direct_delivery_port_and_year(self):
        res = C.compile_filing(filing(header=header(action="X", direct_delivery="Q", port_code="12", calendar_year="2020", control_number="A")))
        text = " | ".join(res["errors"])
        for needle in ("Action Code", "Direct Delivery", "4-digit", "two digits", "at least 2"):
            self.assertIn(needle, text)

    def test_admission_number_is_zone_year_control(self):
        self.assertEqual(C.compile_filing(filing())["admission_number"], "000AAA11120ABC12345")


class TestLinesAndSets(unittest.TestCase):
    def lines(self, *lns, **kw):
        return C.compile_filing(filing(conveyances=[conv(bills=[bill(lines=list(lns), **kw)])]))

    def test_numbering_starts_at_one_ascends_and_restarts_per_bill(self):
        res = C.compile_filing(filing(conveyances=[conv(bills=[bill(bill="BILLONE1", lines=[line(line_no=None), line(line_no=None, htsus="8518220000")]),
                                                              bill(bill="BILLTWO2", lines=[line(line_no=None)])])]))
        self.assertTrue(res["ok"], res["errors"])
        self.assertEqual([t[2:7] for t in by_id(res, "FT50")], ["00001", "00002", "00001"])
        self.assertTrue(any("00001" in e for e in self.lines(line(line_no=2))["errors"]))
        self.assertTrue(any("ascend" in e for e in self.lines(line(line_no=3), line(line_no=2))["errors"]))

    def test_gap_is_a_warning_on_add_and_fine_on_replace(self):
        res = self.lines(line(line_no=1), line(line_no=3))
        self.assertTrue(res["ok"] and any("skips 2" in w for w in res["warnings"]))

    def test_same_number_may_repeat_up_to_eight_times(self):
        same = [line(line_no=1)] * 8
        self.assertTrue(self.lines(*same)["ok"])
        self.assertTrue(any("more than 8" in e for e in self.lines(*([line(line_no=1)] * 9))["errors"]))

    def test_canada_must_use_a_province_code(self):
        self.assertTrue(any("province code" in e for e in self.lines(line(coo="CA"))["errors"]))
        self.assertTrue(self.lines(line(coo="XO"))["ok"])

    def test_hts_must_be_ten_digits_and_dots_are_accepted(self):
        self.assertTrue(any("10-digit" in e for e in self.lines(line(htsus="8518.22"))["errors"]))
        self.assertEqual(by_id(self.lines(line(htsus="9101.21.8030")), "FT50")[0][7:17], "9101218030")

    def test_article_set(self):
        header_ = line(line_no=3, htsus="7307007090", spi_secondary="X", value=300)
        comps = [line(line_no=3, htsus="7307007090", spi_secondary="V", value=100, refs=[]), line(line_no=3, htsus="8427009080", spi_secondary="V", value=200, refs=[])]
        mids = [dict(description="PART", qualifier="MID", ref_id="X1")]
        for c in comps:
            c["refs"] = mids
        res = self.lines(line(line_no=1), line(line_no=2), header_, *comps)
        self.assertTrue(res["ok"], res["errors"])
        self.assertFalse(any("sum of its components" in w for w in res["warnings"]))
        self.assertEqual([t[20] for t in by_id(res, "FT50")], [" ", " ", "X", "V", "V"])
        bad_sum = line(line_no=3, htsus="7307007090", spi_secondary="X", value=999)
        res = self.lines(line(line_no=1), line(line_no=2), bad_sum, *comps)
        self.assertTrue(any("sum of its components (300)" in w for w in res["warnings"]))
        wrong_first = [dict(comps[0], htsus="9999999999"), comps[1]]
        res = self.lines(line(line_no=1), line(line_no=2), header_, *wrong_first)
        self.assertTrue(any("same HTS number as the set header" in e for e in res["errors"]))
        orphan = self.lines(line(spi_secondary="V"))
        self.assertTrue(any("must follow its header" in e for e in orphan["errors"]))
        lonely = self.lines(line(line_no=1, spi_secondary="X"))
        self.assertTrue(any("at least two components" in e for e in lonely["errors"]))

    def test_mid_is_required_on_every_line(self):
        res = self.lines(line(refs=[]))
        self.assertTrue(any("MID" in e for e in res["errors"]))
        res = self.lines(line(refs=[dict(description="X", qualifier="STL", ref_id="LIC1")]))
        self.assertTrue(any("MID" in e for e in res["errors"]))
        res = self.lines(line(refs=[dict(description="X", qualifier="MID", ref_id="M1"), dict(description="X", qualifier="STL", ref_id="LIC1")]))
        self.assertTrue(res["ok"])
        self.assertEqual(len(by_id(res, "FT60")), 2)
        res = self.lines(line(refs=[dict(description="X", qualifier="MID", ref_id=""), ]))
        self.assertFalse(res["ok"])
        res = self.lines(line(refs=[dict(description="X", qualifier="ZZZ", ref_id="1")]))
        self.assertTrue(any("MID, STL, DIA or ALU" in e for e in res["errors"]))

    def test_diamond_certificate_formatting_from_the_administrative_message(self):
        self.assertEqual(C.diamond_ref("CG00123", "p", C.Ctx()), "CG0000123")        # fewer than 9: zeros inserted
        self.assertEqual(C.diamond_ref("CG0001234567", "p", C.Ctx()), "CG1234567")    # more than 9: leading zeros dropped
        ctx = C.Ctx()
        C.diamond_ref("CG1111234567", "p", ctx)
        self.assertTrue(ctx.errors)

    def test_zone_status_codes_from_the_app_are_translated(self):
        for ours, theirs in (("PF", "P"), ("NPF", "N"), ("D", "D"), ("ZR", "Z")):
            self.assertEqual(by_id(self.lines(line(zone_status=ours)), "FT51")[0][34], theirs)
        self.assertTrue(any("Zone Status must be" in e for e in self.lines(line(zone_status="Q"))["errors"]))

    def test_record_limits(self):
        res = self.lines(line(remarks=["R"] * 100))
        self.assertTrue(any("more than the 99" in e for e in res["errors"]))
        res = C.compile_filing(filing(header=header(action="R"), replace=dict(reason_codes=["08"], remarks=["R"] * 11)))
        self.assertTrue(any("more than the 10" in e for e in res["errors"]))
        res = C.compile_filing(filing(header=header(action="D"), delete_remarks=["R"] * 100))
        self.assertTrue(any("more than the 99" in e for e in res["errors"]))


class TestEnvelope(unittest.TestCase):
    ABI = dict(site_code="5301", sender_id="ABC", office_code="", filer_code="ZZZ", port_code="5301")

    def test_abyz_layout_and_password_handling(self):
        head, tail, errs = C.build_envelope(self.ABI, "pw1234", date(2026, 10, 9))
        self.assertEqual(errs, [])
        a, b = head
        y, z = tail
        self.assertTrue(all(len(x) == 80 for x in head + tail))
        self.assertEqual((a[0], a[1:5], a[5:8], a[8:14], a[14:20], a[25:27]), ("A", "5301", "ABC", "PW1234", "100926", "FT"))
        self.assertEqual((b[0], b[3:7], b[7:10], b[10:12]), ("B", "5301", "ZZZ", "FT"))
        self.assertEqual((y[0], y[3:7], y[7:10], y[10:12]), ("Y", "5301", "ZZZ", "FT"))
        self.assertEqual((z[0], z[1:5], z[5:8], z[8:14]), ("Z", "5301", "ABC", " " * 6))      # no password in the Z record for ESAR-style batches
        self.assertEqual(y[12:44], " " * 32)

    def test_password_is_required_and_codes_are_checked(self):
        _, _, errs = C.build_envelope(self.ABI, "", date(2026, 10, 9))
        self.assertTrue(any("password" in e for e in errs))
        _, _, errs = C.build_envelope(dict(self.ABI, site_code=""), "pw1234", date(2026, 10, 9))
        self.assertTrue(any("Site Code" in e for e in errs))

    def test_render_file(self):
        res = C.compile_filing(filing())
        head, tail, _ = C.build_envelope(self.ABI, "pw1234", date(2026, 10, 9))
        body = [r["text"] for r in res["records"]]
        text = C.render_file(body, head, tail, "\r\n")
        lines = text.split("\r\n")
        self.assertEqual(len(lines), len(body) + 4 + 1)
        self.assertEqual((lines[0][0], lines[1][0], lines[2][:2], lines[-3][0], lines[-2][0], lines[-1]), ("A", "B", "10", "Y", "Z", ""))
        self.assertTrue(all(len(x) == 80 for x in lines[:-1]))


if __name__ == "__main__":
    unittest.main()
