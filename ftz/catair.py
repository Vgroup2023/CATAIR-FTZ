"""ACE CATAIR Foreign Trade Zone e214 "FT (Input)" record builder.

Source of truth for every position, length, class and designation below:
  * "ACE CATAIR Foreign Trade Zone Admission (e214)", Version 3.1.3, August 2026, Pub # 0875-0419
    (pdf sha256 87d7b1df09223aae6c8572ba159755b2e034d09d4bec48d5dbb991546ef9ef8c)
  * "ABI Batch and Block Control" (A/B/Y/Z records), version 19, 19 Oct 2021, marked DRAFT by CBP
    (pdf sha256 146d9c7906d46b6d0a0708db7294f6043aa6766b4d4a093b5963a8fd7f6a6e3d)
`tools/verify_catair.py` re-checks this table against the CBP PDF, so a new CATAIR version is a diff, not a surprise.

This module only *prepares* the fixed-width 80-character records. It does not transmit anything to CBP.
"""
import re
import string
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

SOURCE = {
    "ft": {"title": "ACE CATAIR Foreign Trade Zone Admission (e214)", "version": "3.1.3", "date": "August 2026", "pub": "0875-0419"},
    "envelope": {"title": "ACE CATAIR ABI Batch and Block Control", "version": "19", "date": "October 19, 2021 (CBP draft)",
                 "pub": "0875-0419"},
}
RECORD_LEN = 80
APP_ID = "FT"

# ----------------------------------------------------------------------------------------------- layouts
# (key, label, start, end, class, designation, extras)   key None = filler.  Control identifiers use key "_id".
def _f(key, label, start, end, cls, req, **x):
    return dict(key=key, label=label, start=start, end=end, len=end - start + 1, cls=cls, req=req, **x)


def _ctl(n):
    return _f("_id", "Control Identifier", 1, 2, "N", "M", fixed=f"{n:02d}")


def _fill(start, end):
    return _f(None, "Filler", start, end, "S", "M")


LAYOUT = {
    "FT10": {"title": "Admission header", "fields": [
        _ctl(10),
        _f("action", "Action Code", 3, 3, "A", "M", help="A Add · D Delete · R Replace · S Merchandise Zone Status Change", choices=["A", "D", "R", "S"]),
        _f("zone_id", "Zone ID", 4, 12, "AN", "M", help="Legacy 7 characters (FTZ ID 3 digits + sub-zone 2 + site 2) or expanded 9 (3 + 3 + 3), left-justified"),
        _f("calendar_year", "Calendar Year", 13, 14, "N", "M", help="YY"),
        _f("control_number", "Control Number", 15, 22, "AN", "M", help="Unique and sequential, minimum 2 characters. Zone ID + year + control number = the admission number"),
        _f("expanded", "Expanded Zone ID Indicator", 23, 23, "AN", "M", derived=True, help="Y when a 9-character Zone ID is sent, N for 7. Set automatically"),
        _f("port_code", "Port Code", 24, 27, "N", "M", help="U.S. Census Schedule D code of the port where the FTZ is located"),
        _f("direct_delivery", "Direct Delivery Indicator", 28, 28, "A", "M", choices=["N", "Y"], help="Y only for a FIRMS location approved for Direct Delivery"),
        _f("abi_filer", "ABI Filer Code", 29, 31, "AN", "M", help="Filer code of the submitter"),
        _f("abi_routing", "ABI Routing Code", 32, 40, "AN", "O", help="Optional broker-download routing, format DDPPFLROF"),
        _f("zone_operator", "Zone Operator Identifier", 41, 52, "X", "M", help="Importer of Record number of the Zone Operator, NN-NNNNNNNNN. Must hold an active type 4 FTZ bond"),
        _f("firms", "FIRMS Identifier", 53, 56, "AN", "M", help="FIRMS code of the admission FTZ site location"),
        _f("applicant", "Applicant for Admission", 57, 68, "X", "C", help="Importer of Record ID of the applicant. Required only when different from the Zone Operator"),
        _fill(69, 80)]},
    "FT11": {"title": "Replace request", "fields": [
        _ctl(11),
        _f("contact_name", "Contact Name", 3, 42, "AN", "O"),
        _f("contact_phone", "Contact Phone", 43, 57, "N", "O", pack="left", help="Digits only"),
        _f("reason", "Reason Code", 58, 59, "N", "M", help="01 Change/Add Conveyance · 02 Delete Conveyance · 03 Change/Add Bill · 04 Delete Bill · 05 Change/Add HTS Line · 06 Delete HTS Line · 07 Change Admitted Quantity · 08 Other (needs FT12) · 09 Cancel/Add PTT · 10 Replace Temporary Deposit"),
        _f("reason_more", "Additional Reason Codes", 60, 73, "N", "O", pack="codes", help="Up to 7 more two-digit codes; none may repeat"),
        _fill(74, 80)]},
    "FT12": {"title": "Replace remarks", "fields": [
        _ctl(12),
        _f("remarks", "Remarks", 3, 80, "X", "M", help="Open-text remarks about the replace transaction (up to 10 records)")]},
    "FT20": {"title": "Conveyance", "fields": [
        _ctl(20),
        _f("admission_type", "Admission Type", 3, 3, "A", "M", choices=["A", "C", "D", "O", "T", "Z"],
           help="A Regular · C Status Change · D Domestic · O Overage · T Temporary Deposit · Z Zone to Zone"),
        _f("mot", "Mode of Transportation", 4, 5, "N", "C", help="Valid codes are in ACE CATAIR Appendix B (10/11 vessel and 40/41 air are the ones this chapter relies on). Blank for D, O, Z"),
        _f("scac", "SCAC / Airline Carrier Code", 6, 9, "A", "C", soft="AN", help="SCAC of the importing carrier; for air the 2- or 3-character airline code. Blank for D, O, Z"),
        _f("conveyance_name", "Conveyance Name", 10, 32, "AN", "C", help="Vessel name for MOT 10/11, otherwise the transportation company"),
        _f("voyage", "Voyage/Trip/Flight Number", 33, 47, "AN", "C", help="Air flight numbers are at least 4 characters, zero-padded on the left"),
        _f("export_date", "Export Date", 48, 55, "N", "C", date=True, help="Must be on or before the import date"),
        _f("import_date", "Import Date", 56, 63, "N", "C", date=True, help="Arrival at the first U.S. port of unlading"),
        _f("port_unlading", "Port of Unlading", 64, 67, "N", "C", help="U.S. Census Schedule D code"),
        _f("scheduled_arrival", "Scheduled Date of Arrival", 68, 75, "N", "C", date=True, help="Needed for air split shipments: the scheduled arrival of that split part"),
        _fill(76, 80)]},
    "FT40": {"title": "Bill of lading", "fields": [
        _ctl(40),
        _f("bill", "Bill of Lading or Air Waybill", 3, 37, "AN", "M", help="6 to 35 characters. For an Overage admission: FIRMS + ADJ + YYMMDD + 3-digit sequence"),
        _f("house_bill", "House Bill", 38, 57, "AN", "C", help="Mandatory for air shipments with a manifested Master/House combination; blank for other modes"),
        _f("quantity", "Quantity", 58, 67, "N", "C", help="Smallest exterior packaging unit of the lowest-level bill; required when an in-bond number is reported"),
        _f("country_export", "Country of Export", 68, 69, "AN", "C", help="ISO code"),
        _f("load_port", "Foreign Load Port", 70, 74, "N", "C", help="Census Schedule K code. Only for MOT 10 or 11"),
        _fill(75, 80)]},
    "FT41": {"title": "In-bond number", "fields": [
        _ctl(41),
        _f("number", "I.T. Number", 3, 37, "AN", "M", help="9 to 23 characters: 9 for a 7512 in-bond, 11 for sea/rail AMS, up to 23 for air master + house"),
        _fill(38, 80)]},
    "FT42": {"title": "Bonded carrier (Permit To Transfer)", "fields": [
        _ctl(42),
        _f("carrier_ior", "IRS Identifier (Bonded Carrier)", 3, 14, "X", "C", help="Importer of Record number of the bonded carrier nominated for the local transfer; needs an active Activity Type 2 bond (or the Zone Operator's type 4)"),
        _fill(15, 80)]},
    "FT43": {"title": "Container / equipment", "fields": [
        _ctl(43),
        _f("container", "Container Number", 3, 17, "AN", "M"),
        _fill(18, 80)]},
    "FT50": {"title": "HTS line", "fields": [
        _ctl(50),
        _f("line_no", "Line Item Number", 3, 7, "N", "M", help="Starts at 00001 for each bill and ascends. All items of an Article Set share one number"),
        _f("htsus", "Harmonized Tariff Schedule Number", 8, 17, "AN", "M", help="10-digit HTS number"),
        _f("spi", "Special Programs Indicator (SPI)", 18, 18, "AN", "O", help="See AE Table 8 in the ACE CATAIR Entry Summary chapter"),
        _f("spi_country", "SPI Country", 19, 20, "AN", "O"),
        _f("spi_secondary", "SPI Secondary", 21, 21, "AN", "O", help="X Article Set header · V Article Set component · F folklore · G made-to-measure suit · H chapter 61/62 special access · M textile sample"),
        _f("coo", "Country of Origin", 22, 23, "AN", "M", help="ISO code. For Canada use the Census province code (XA, XB, XC, XM, XN, XO, XP, XQ, XS, XT, XV, XW, XY), never CA"),
        _f("qty1", "Quantity 1", 24, 35, "N", "M", implied=2, help="Two implied decimals; up to two decimal places allowed"),
        _f("uom1", "Unit of Measure 1", 36, 38, "AN", "C", help="Primary statistical unit; if it is X, the FTZ line unit from your inventory records"),
        _f("qty2", "Quantity 2", 39, 50, "N", "O", implied=2, blank="zero", help="Secondary statistical quantity, two implied decimals"),
        _f("uom2", "Unit of Measure 2", 51, 53, "AN", "O"),
        _f("quota", "Quota Category", 54, 56, "AN", "O"),
        _f("pn_disclaimer", "PN Disclaimer Flag", 57, 57, "AN", "O", choices=["", "Y"], help="Y when the HTS is disclaimable under FDA Prior Notice rules"),
        _fill(58, 80)]},
    "FT51": {"title": "HTS line values", "fields": [
        _ctl(51),
        _f("weight", "Weight", 3, 12, "N", "M", help="Gross weight of the shipment, whole number"),
        _f("value", "Value", 13, 24, "N", "M", help="Whole U.S. dollars. For an Article Set header: the sum of its components"),
        _f("charges", "Charges", 25, 34, "N", "M", help="Transportation charges to the FTZ, whole U.S. dollars"),
        _f("zone_status", "Zone Status", 35, 35, "A", "M", choices=["P", "N", "D", "Z"], help="P Privileged Foreign · N Non-privileged Foreign · D Domestic · Z Zone Restricted"),
        _f("hmf", "Harbor Maintenance Fee", 36, 43, "N", "M", implied=2, help="Zero, or the fee in dollars and cents"),
        _f("existing_status", "Existing Zone Status", 44, 44, "A", "C", choices=["", "N"], help="Status Change only: the existing status (N)"),
        _f("requested_status", "Requested Zone Status", 45, 45, "A", "C", choices=["", "P", "Z"], help="Status Change only: P or Z"),
        _f("affected_qty", "FTZ Line Item Quantity to be Affected", 46, 57, "N", "C", help="Status Change only: whole number above zero"),
        _f("inventory_uom", "Inventory Unit of Measure", 58, 60, "AN", "C", help="Status Change only"),
        _fill(61, 80)]},
    "FT60": {"title": "Line reference", "fields": [
        _ctl(60),
        _f("description", "Description", 3, 47, "AN", "M", help="Line-level description of the merchandise (45 characters, letters and numbers)"),
        _f("qualifier", "Reference Qualifier", 48, 50, "AN", "C", choices=["", "MID", "STL", "DIA", "ALU"], help="MID Manufacturer ID · STL steel licence · DIA Kimberley diamond certificate · ALU aluminium licence"),
        _f("ref_id", "Reference ID", 51, 72, "AN", "C"),
        _fill(73, 80)]},
    "FT61": {"title": "Line remarks", "fields": [
        _ctl(61),
        _f("remarks", "Remarks", 3, 80, "AN", "M", help="Free-form description or other pertinent information (up to 99 records)")]},
}

# Block/batch control records (separate CBP chapter, ESAR-style layout; FT is not an e-Manifest transaction).
ENVELOPE = {
    "A": [_f("_id", "Control Identifier", 1, 1, "A", "M", fixed="A"), _f("site", "Sender/Receiver Site Code", 2, 5, "AN", "M"),
          _f("sender", "Sender/Receiver ID Code", 6, 8, "AN", "M"), _f("password", "Communication Password", 9, 14, "AN", "M"),
          _f("date", "Transmission Date", 15, 20, "D", "O"), _fill(21, 25), _f("app", "Application Identifier Code", 26, 27, "AN", "M", fixed=APP_ID),
          _fill(28, 37), _f("office", "Sender/Receiver Office Code", 38, 39, "AN", "C"), _fill(40, 59),
          _f("user_text", "Transmitter's User Data Text", 60, 80, "X", "O")],
    "B": [_f("_id", "Control Identifier", 1, 1, "A", "M", fixed="B"), _fill(2, 3), _f("port", "Processing District/Port Code", 4, 7, "AN", "M"),
          _f("filer", "Filer Code", 8, 10, "AN", "M"), _f("app", "Application Identifier Code", 11, 12, "AN", "M", fixed=APP_ID), _fill(13, 44),
          _f("office", "Processing Filer Office Code", 45, 46, "AN", "C"),
          _f("rp_port", "Remote Preparer District/Port Code", 47, 50, "AN", "C"), _f("rp_filer", "Remote Preparer Filer Code", 51, 53, "AN", "C"),
          _f("rp_office", "Remote Preparer Office Code", 54, 55, "AN", "C"), _f("rp_flag", "Remotely Filed Indicator", 56, 56, "AN", "C"),
          _fill(57, 59), _f("user_text", "Filer's User Data Text", 60, 80, "X", "O")],
    "Y": [_f("_id", "Control Identifier", 1, 1, "A", "M", fixed="Y"), _fill(2, 3), _f("port", "Processing District/Port Code", 4, 7, "AN", "M"),
          _f("filer", "Filer Code", 8, 10, "AN", "M"), _f("app", "Application Identifier Code", 11, 12, "AN", "M", fixed=APP_ID), _fill(13, 44),
          _f("office", "Processing Filer Office Code", 45, 46, "AN", "C"), _f(None, "Filler", 47, 80, "S", "C")],
    "Z": [_f("_id", "Control Identifier", 1, 1, "A", "M", fixed="Z"), _f("site", "Sender/Receiver Site Code", 2, 5, "AN", "M"),
          _f("sender", "Sender/Receiver ID Code", 6, 8, "AN", "M"), _fill(9, 14), _f("date", "Transmission Date", 15, 20, "D", "C"), _fill(21, 37),
          _f("office", "Sender/Receiver Office Code", 38, 39, "AN", "C"), _fill(40, 80)],
}

# ----------------------------------------------------------------------------------------------- usage maps
# (record, description, requirement, max occurrence, loop repeat, depth)
USAGE_MAPS = {
    "main": ("Input Records Usage Map (FT)", [
        ("A, B", "Transaction Control Headers", "M", "1", "", 0),
        ("FT10", "Basic information on the e214 Admission, such as Admission Number", "M", "1", "", 0),
        ("FT11", "Additional information for a replace transaction", "C", "1", "", 1),
        ("FT12", "Remarks related to a replace transaction", "C", "10", "", 1),
        ("FT20", "Admission Type and Conveyance information", "C", "1", "999", 1),
        ("FT40", "Bill of Lading and House Bill numbers", "C", "1", "9,999", 2),
        ("FT41", "In-bond number", "C", "9,999", "", 3),
        ("FT42", "Importer of Record of the bonded carrier for the Permit To Transfer", "C", "9,999", "", 3),
        ("FT43", "Container or Equipment Number", "C", "9,999", "", 3),
        ("FT50", "HTS Line Item data: HTS number, origin, quantities, units", "C", "1", "9,999", 3),
        ("FT51", "Weight, value, charges, zone status and Harbor Maintenance Fee for the preceding FT50", "C", "1", "", 4),
        ("FT60", "Reference information for the preceding FT50 (MID is required)", "C", "9,999", "", 4),
        ("FT61", "Free-form description or remarks for the preceding FT50", "O", "99", "", 4),
        ("Y, Z", "Transaction Control Trailers", "M", "1", "", 0)]),
    "S": ("Usage map: Status Change transaction", [
        ("A, B", "Transaction Control Headers", "M", "1", "", 0), ("FT10", "Admission header (Action Code S)", "M", "1", "", 0),
        ("FT20", "Admission Type C and conveyance", "M", "1", "999", 1), ("FT40", "Bill of lading", "M", "1", "9,999", 2),
        ("FT50", "HTS line affected", "M", "1", "9,999", 3), ("FT51", "Existing / requested status and quantity affected", "M", "1", "", 4),
        ("Y, Z", "Transaction Control Trailers", "M", "1", "", 0)]),
    "T": ("Usage map: Temporary Deposit transaction", [
        ("A, B", "Transaction Control Headers", "M", "1", "", 0), ("FT10", "Admission header", "M", "1", "", 0),
        ("FT20", "Admission Type T and conveyance", "C", "1", "999", 1), ("FT40", "Bill of lading", "M", "1", "9,999", 2),
        ("FT41", "In-bond number", "C", "9,999", "", 3), ("FT42", "Bonded carrier for the Permit To Transfer", "C", "9,999", "", 3),
        ("FT43", "Container or Equipment Number", "C", "9,999", "", 3), ("Y, Z", "Transaction Control Trailers", "M", "1", "", 0)]),
    "D": ("Usage map: Delete transaction", [
        ("A, B", "Transaction Control Headers", "M", "1", "", 0), ("FT10", "Admission header (Action Code D)", "M", "1", "", 0),
        ("FT61", "Free-form comments (e.g. the entry or export document number)", "O", "99", "", 1),
        ("Y, Z", "Transaction Control Trailers", "M", "1", "", 0)]),
}

LIMITS = {"conveyances": 999, "bills": 9999, "lines": 9999, "inbonds": 9999, "carriers": 9999, "containers": 9999,
          "refs": 9999, "remarks61": 99, "remarks12": 10, "reasons": 8}
REASONS = {f"{i:02d}": t for i, t in enumerate(["Change/Add Conveyance(s)", "Delete Conveyance(s)", "Change/Add Bill of Lading(s)",
                                                  "Delete Bill of Lading(s)", "Change/Add HTS Line(s)", "Delete HTS Line(s)",
                                                  "Change Admitted Quantity", "Other", "Cancel/Add PTT", "Replace Temporary Deposit"], 1)}
ADMISSION_TYPES = {"A": "Regular Admission", "C": "Status Change", "D": "Domestic", "O": "Overage Admission",
                   "T": "Temporary Deposit", "Z": "Zone to Zone"}
ZONE_STATUS = {"PF": "P", "NPF": "N", "D": "D", "ZR": "Z", "P": "P", "N": "N", "Z": "Z"}
CANADA_PROVINCES = {"XA", "XB", "XC", "XM", "XN", "XO", "XP", "XQ", "XS", "XT", "XV", "XW", "XY"}
SPI_SECONDARY = {"X", "V", "F", "G", "H", "M"}
QUALIFIERS = {"MID", "STL", "DIA", "ALU"}
AIR_MOT, VESSEL_MOT = {"40", "41"}, {"10", "11"}

_SPECIALS = set("!@#$%^&*()-_=+[{]}\\|;:'\",<.>/?`~")
_AN = set(string.ascii_uppercase + string.digits + " ")
CHARSETS = {"A": set(string.ascii_uppercase), "AN": _AN, "X": _AN | _SPECIALS, "N": set(string.digits), "D": set(string.digits)}

IOR_PATTERNS = (re.compile(r"^\d{2}-\d{9}$"), re.compile(r"^\d{3}-\d{2}-\d{4}$"))


# ----------------------------------------------------------------------------------------------- helpers
class Ctx:
    def __init__(self):
        self.errors, self.warnings, self.records, self.counts = [], [], [], {}

    def err(self, path, msg):
        self.errors.append(f"{path}: {msg}")

    def warn(self, path, msg):
        self.warnings.append(f"{path}: {msg}")


def blank(v):
    return v is None or (isinstance(v, str) and v.strip() == "") or (isinstance(v, (list, tuple)) and not v)


def s(v):
    return "" if v is None else str(v).strip()


def up(v):
    return s(v).upper()


def to_decimal(v):
    try:
        return Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        return None


def whole(v, path, ctx, label):
    """Whole-number field: round half-up and say so, rather than silently changing money."""
    d = to_decimal(v)
    if d is None or d < 0:
        ctx.err(path, f"{label} must be a number, zero or more")
        return None
    r = d.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if r != d:
        ctx.warn(path, f"{label} {d} was rounded to {r}: CATAIR carries whole numbers only")
    return int(r)


def ccyymmdd(v, path, ctx, label):
    t = s(v)
    if not t:
        return ""
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(t, fmt).strftime("%Y%m%d")
        except ValueError:
            pass
    ctx.err(path, f"{label} must be a real date (YYYY-MM-DD)")
    return ""


def clean_text(v, limit=None):
    """Make free text CATAIR-safe (upper case, letters/numbers/space). Returns (text, changed)."""
    t = up(v)
    out = re.sub(r"[^A-Z0-9 ]", " ", t)
    out = re.sub(r" {2,}", " ", out).strip()
    if limit:
        out = out[:limit].rstrip()
    return out, out != s(v)


def format_field(f, raw, path, ctx, required=False, force_blank=False):
    """Return exactly f['len'] characters for one field, recording any problem against `path`."""
    n, cls, label = f["len"], f["cls"], f["label"]
    if cls == "S":
        return " " * n
    if "fixed" in f:
        return f["fixed"].ljust(n)
    if force_blank or blank(raw):
        if required and not force_blank:
            ctx.err(path, f"{label} (pos {f['start']}-{f['end']}) is required")
        return ("0" * n) if (f.get("blank") == "zero" and cls == "N") else " " * n
    if f.get("pack") == "codes":
        codes = [up(c) for c in raw] if isinstance(raw, (list, tuple)) else [up(raw)]
        txt = "".join(codes)
        if not all(re.fullmatch(r"\d{2}", c) for c in codes) or len(txt) > n:
            ctx.err(path, f"{label} must be two-digit codes, at most {n // 2} of them")
            return " " * n
        return txt.ljust(n)
    if cls == "N" and not f.get("pack"):
        d = to_decimal(raw)
        if d is None or d < 0:
            ctx.err(path, f"{label} (pos {f['start']}-{f['end']}) must be a number, zero or more")
            return " " * n
        k = f.get("implied", 0)
        scaled = d * (Decimal(10) ** k)
        if scaled != scaled.to_integral_value():
            ctx.err(path, f"{label} (pos {f['start']}-{f['end']}) allows only {k} decimal place(s)" if k else f"{label} (pos {f['start']}-{f['end']}) must be a whole number")
            return " " * n
        digits = str(int(scaled))
        if len(digits) > n:
            ctx.err(path, f"{label} is too large for {n} positions")
            return " " * n
        return digits.zfill(n)
    text = up(raw) if cls != "N" else re.sub(r"\D", "", s(raw))
    if f.get("pack") == "left":
        if not text.isdigit() or len(text) > n:
            ctx.err(path, f"{label} (pos {f['start']}-{f['end']}) must be up to {n} digits")
            return " " * n
        return text.ljust(n)
    if len(text) > n:
        ctx.err(path, f"{label} (pos {f['start']}-{f['end']}) is {len(text)} characters; the field holds {n}")
        return " " * n
    bad = sorted({c for c in text if c not in CHARSETS[f.get("soft", cls)]})
    if bad:
        shown = " ".join(repr(c) for c in bad[:6])
        kind = {"A": "letters A-Z only", "AN": "letters, numbers and spaces only", "X": "letters, numbers and standard keyboard symbols", "D": "digits (MMDDYY)"}[cls]
        ctx.err(path, f"{label} (pos {f['start']}-{f['end']}) allows {kind}; found {shown}")
        return " " * n
    return text.ljust(n)


def make_record(ctx, rid, path, values, need=(), blank_keys=(), layout=None):
    fields = (layout or LAYOUT[rid]["fields"])
    chars = [" "] * RECORD_LEN
    for f in fields:
        key = f["key"]
        if key == "_id":
            text = format_field(f, None, path, ctx)
        elif key is None:
            text = " " * f["len"]
        else:
            required = f["req"] == "M" or key in need
            text = format_field(f, values.get(key), path, ctx, required=required and not f.get("derived"), force_blank=key in blank_keys)
        chars[f["start"] - 1:f["end"]] = list(text)
    line = "".join(chars)
    assert len(line) == RECORD_LEN, (rid, len(line))
    return line


def add(ctx, rid, path, values, **kw):
    line = make_record(ctx, rid, path, values, **kw)
    ctx.records.append({"id": rid, "path": path, "text": line})
    ctx.counts[rid] = ctx.counts.get(rid, 0) + 1
    return line


def check_count(ctx, path, what, n, limit):
    if n > limit:
        ctx.err(path, f"{n} {what} is more than the {limit:,} CATAIR allows")


def ior_check(ctx, path, label, v):
    t = up(v)
    if t and not any(p.match(t) for p in IOR_PATTERNS):
        ctx.warn(path, f"{label} '{t}' is not in the NN-NNNNNNNNN format (or NNN-NN-NNNN) used for Importer of Record numbers")


# ----------------------------------------------------------------------------------------------- the transaction
def zone_info(raw, path, ctx):
    z = up(raw).replace(" ", "")
    if not z:
        return "", ""
    if re.fullmatch(r"\d{3}[A-Z0-9]{4}", z):
        return z, "N"
    if re.fullmatch(r"\d{3}[A-Z0-9]{6}", z):
        return z, "Y"
    ctx.err(path, "Zone ID must be 7 characters (3 digits + 2 + 2) or 9 characters (3 digits + 3 + 3), letters and numbers only")
    return z, ""


def admission_number(h):
    return f"{up(h.get('zone_id')).replace(' ', '')}{s(h.get('calendar_year'))}{up(h.get('control_number'))}"


def map_key(action, types):
    if action == "S":
        return "S"
    if action == "D":
        return "D"
    if types and set(types) == {"T"}:
        return "T"
    return "main"


def compile_filing(data):
    """Validate and build the FT (Input) records for one e214 transaction. Never raises on bad data."""
    ctx = Ctx()
    data = data if isinstance(data, dict) else {}
    h = data.get("header") or {}
    action = up(h.get("action"))
    P = "FT10 Header"
    if action not in ("A", "D", "R", "S"):
        ctx.err(P, "Action Code must be A (Add), D (Delete), R (Replace) or S (Status Change)")
    zone, expanded = zone_info(h.get("zone_id"), P, ctx)
    year = s(h.get("calendar_year"))
    if year and not re.fullmatch(r"\d{2}", year):
        ctx.err(P, "Calendar Year must be two digits (YY)")
    ctl = up(h.get("control_number"))
    if ctl and len(ctl) < 2:
        ctx.err(P, "Control Number must be at least 2 characters")
    if h.get("port_code") and not re.fullmatch(r"\d{4}", s(h.get("port_code"))):
        ctx.err(P, "Port Code must be a 4-digit Census Schedule D code")
    dd = up(h.get("direct_delivery")) or "N"
    if dd not in ("N", "Y"):
        ctx.err(P, "Direct Delivery Indicator must be N or Y")
    op, app = up(h.get("zone_operator")), up(h.get("applicant"))
    ior_check(ctx, P, "Zone Operator Identifier", op)
    ior_check(ctx, P, "Applicant for Admission", app)
    if app and app == op:
        ctx.warn(P, "Applicant for Admission is the same as the Zone Operator, so it was left blank as CATAIR requires")
        app = ""
    v10 = dict(action=action, zone_id=zone, calendar_year=year, control_number=ctl, expanded=expanded, port_code=h.get("port_code"),
               direct_delivery=dd, abi_filer=h.get("abi_filer"), abi_routing=h.get("abi_routing"), zone_operator=op,
               firms=h.get("firms"), applicant=app)
    add(ctx, "FT10", P, v10)

    convs = data.get("conveyances") or []
    result = {"admission_number": admission_number({**h, "zone_id": zone, "calendar_year": year, "control_number": ctl}), "action": action}

    if action == "D":
        if convs:
            ctx.warn("Transaction", "A Delete sends only FT10 and optional FT61 comments; the conveyances were left out")
        rem = [s(x) for x in (data.get("delete_remarks") or []) if s(x)]
        check_count(ctx, "FT61", "comments", len(rem), LIMITS["remarks61"])
        for i, r in enumerate(rem, 1):
            add(ctx, "FT61", f"FT61 comment {i}", {"remarks": r})
        return finish(ctx, result, "D")

    if action == "R":
        rp = data.get("replace") or {}
        reasons = [up(x) for x in (rp.get("reason_codes") or []) if s(x)]
        PR = "FT11 Replace"
        if not reasons:
            ctx.err(PR, "A Replace needs at least one Reason Code")
        if len(set(reasons)) != len(reasons):
            ctx.err(PR, "Reason codes must not repeat")
        for c in reasons:
            if c not in REASONS:
                ctx.err(PR, f"Reason Code '{c}' is not valid (01-10)")
        check_count(ctx, PR, "reason codes", len(reasons), LIMITS["reasons"])
        add(ctx, "FT11", PR, dict(contact_name=rp.get("contact_name"), contact_phone=rp.get("contact_phone"),
                                  reason=reasons[0] if reasons else "", reason_more=reasons[1:]))
        remarks = [s(x) for x in (rp.get("remarks") or []) if s(x)]
        if "08" in reasons and not remarks:
            ctx.err("FT12 Remarks", "Reason Code 08 (Other) requires at least one FT12 remark explaining it")
        check_count(ctx, "FT12", "remarks", len(remarks), LIMITS["remarks12"])
        for i, r in enumerate(remarks, 1):
            add(ctx, "FT12", f"FT12 remark {i}", {"remarks": r})
    elif data.get("replace") and any(not blank(v) for v in (data.get("replace") or {}).values()):
        ctx.warn("Transaction", "FT11/FT12 are used only when the Action Code is R; they were left out")

    if not convs:
        ctx.err("Transaction", "At least one conveyance is required for an Add, Replace or Status Change")
    check_count(ctx, "Transaction", "conveyances", len(convs), LIMITS["conveyances"])
    types = []
    for ci, cv in enumerate(convs, 1):
        types.append(up(cv.get("admission_type")))
        build_conveyance(ctx, ci, cv, h, action, v10)
    result["usage_map"] = map_key(action, types)
    return finish(ctx, result, result["usage_map"])


def build_conveyance(ctx, ci, cv, h, action, v10):
    P = f"Conveyance {ci}"
    atype = up(cv.get("admission_type"))
    if atype not in ADMISSION_TYPES:
        ctx.err(P, "Admission Type must be A, C, D, O, T or Z")
    if action == "S" and atype != "C":
        ctx.err(P, "A Status Change must use Admission Type C")
    if action in ("A", "R") and atype == "C":
        ctx.err(P, "Admission Type C is only for a Status Change (Action Code S)")
    quiet = atype in ("D", "O", "Z")                     # CATAIR: positions 4-75 are space filled for these
    mot = s(cv.get("mot"))
    if mot and not re.fullmatch(r"\d{2}", mot):
        ctx.err(P, "Mode of Transportation must be a 2-digit code (valid codes: ACE CATAIR Appendix B)")
    air, vessel = mot in AIR_MOT, mot in VESSEL_MOT
    strict = atype == "A"
    conv_keys = ("mot", "scac", "conveyance_name", "voyage", "export_date", "import_date", "port_unlading")
    given = [k for k in conv_keys + ("scheduled_arrival",) if not blank(cv.get(k))]
    if quiet and given:
        ctx.warn(P, f"Admission Type {atype} sends no conveyance details; those fields were left blank")
    exp, imp = ccyymmdd(cv.get("export_date"), P, ctx, "Export Date"), ccyymmdd(cv.get("import_date"), P, ctx, "Import Date")
    sched = ccyymmdd(cv.get("scheduled_arrival"), P, ctx, "Scheduled Date of Arrival")
    if exp and imp and exp > imp:
        ctx.err(P, "Export Date must be on or before the Import Date")
    voyage = up(cv.get("voyage"))
    if air and voyage and len(voyage) < 4:
        voyage = voyage.zfill(4)
        ctx.warn(P, f"Air flight number padded with zeros on the left to {voyage}")
    scac = up(cv.get("scac"))
    if scac and not re.fullmatch(r"[A-Z]{2,4}", scac):
        if air and re.fullmatch(r"[A-Z0-9]{2,3}", scac):
            ctx.warn(P, f"Airline code {scac} contains a digit; CATAIR defines this field as letters only: confirm it with your ABI software vendor")
        else:
            ctx.err(P, "SCAC / Airline Carrier Code must be 2 to 4 letters")
    need = set(conv_keys) if strict else set()
    if atype == "T":
        for k in conv_keys:
            if blank(cv.get(k)):
                ctx.warn(P, f"Temporary Deposit usually reports {k.replace('_', ' ')}")
    vals = dict(admission_type=atype, mot=mot, scac=scac, conveyance_name=cv.get("conveyance_name"), voyage=voyage,
                export_date=exp, import_date=imp, port_unlading=cv.get("port_unlading"), scheduled_arrival=sched)
    add(ctx, "FT20", P, vals, need=need, blank_keys=set(conv_keys + ("scheduled_arrival",)) if quiet else ())
    if cv.get("port_unlading") and not re.fullmatch(r"\d{4}", s(cv.get("port_unlading"))):
        ctx.err(P, "Port of Unlading must be a 4-digit Census Schedule D code")
    bills = cv.get("bills") or []
    if not bills:
        ctx.err(P, "At least one bill of lading (FT40) is required")
    check_count(ctx, P, "bills of lading", len(bills), LIMITS["bills"])
    for bi, b in enumerate(bills, 1):
        build_bill(ctx, f"{P} › Bill {bi}", b, atype, mot, air, vessel, action, v10)


def build_bill(ctx, P, b, atype, mot, air, vessel, action, v10):
    bill = up(b.get("bill"))
    if bill and not (6 <= len(bill) <= 35):
        ctx.err(P, "Bill of Lading must be 6 to 35 characters")
    if atype == "O":
        m = re.fullmatch(r"([A-Z0-9]{4})ADJ(\d{6})(\d{3})", bill)
        if not m:
            ctx.err(P, "An Overage bill must be 16 characters: FIRMS code + ADJ + YYMMDD + 3-digit sequence (e.g. O920ADJ161028001)")
        elif up(v10.get("firms")) and m.group(1) != up(v10.get("firms")):
            ctx.err(P, f"The Overage bill must start with the site FIRMS code {up(v10.get('firms'))}")
    inb = [up(x) for x in (b.get("inbond_numbers") or []) if s(x)]
    car = [up(x) for x in (b.get("bonded_carriers") or []) if s(x)]
    cont = [up(x) for x in (b.get("containers") or []) if s(x)]
    house = up(b.get("house_bill"))
    if house and not air:
        ctx.warn(P, "House Bill is for air shipments (MOT 40/41); it was left blank")
        house = ""
    qty_given = not blank(b.get("quantity"))
    need, blank_keys = set(), set()
    if inb:                                              # an in-bond number makes Quantity mandatory for every admission type
        need.add("quantity")
    elif atype in ("O", "C", "D", "T"):                  # otherwise it is space filled for these types
        blank_keys.add("quantity")
        if qty_given:
            ctx.warn(P, f"Quantity is not reported for Admission Type {atype} without an in-bond number; it was left blank")
    elif atype == "A":
        need.add("quantity")
    if atype == "A":
        need.add("country_export")
        if vessel:
            need.add("load_port")
    else:                                                # Country of Export / Foreign Load Port: space fill for O, C, D, T, Z
        blank_keys.update(("country_export", "load_port"))
        if not blank(b.get("country_export")) or not blank(b.get("load_port")):
            ctx.warn(P, f"Country of Export and Foreign Load Port are not reported for Admission Type {atype}; they were left blank")
    if atype == "A" and not vessel and not blank(b.get("load_port")):
        ctx.warn(P, "Foreign Load Port is only sent for vessel shipments (MOT 10/11); it was left blank")
        blank_keys.add("load_port")
    if not air:
        blank_keys.add("house_bill")
    load = b.get("load_port")
    if not blank(b.get("country_export")) and not re.fullmatch(r"[A-Za-z0-9]{2}", s(b.get("country_export"))):
        ctx.err(P, "Country of Export must be a 2-character ISO code")
    add(ctx, "FT40", P, dict(bill=bill, house_bill=house, quantity=b.get("quantity"), country_export=b.get("country_export"), load_port=load),
        need=need, blank_keys=blank_keys)
    for k, items, rid, key, lim in (("inbond", inb, "FT41", "number", "inbonds"), ("carrier", car, "FT42", "carrier_ior", "carriers"),
                                   ("container", cont, "FT43", "container", "containers")):
        check_count(ctx, P, {"inbond": "in-bond numbers", "carrier": "bonded carriers", "container": "containers"}[k], len(items), LIMITS[lim])
    for i, n in enumerate(inb, 1):
        if not (9 <= len(n) <= 23):
            ctx.err(f"{P} › FT41 {i}", "I.T. Number must be 9 to 23 characters")
        add(ctx, "FT41", f"{P} › FT41 {i}", {"number": n})
    for i, n in enumerate(car, 1):
        ior_check(ctx, f"{P} › FT42 {i}", "Bonded carrier", n)
        add(ctx, "FT42", f"{P} › FT42 {i}", {"carrier_ior": n})
    if car and v10.get("direct_delivery") == "Y":
        ctx.warn(P, "FT42 starts a Permit To Transfer for non-Direct-Delivery admissions; for Direct Delivery the PTT is normally filed first with an FZ transaction")
    for i, n in enumerate(cont, 1):
        add(ctx, "FT43", f"{P} › FT43 {i}", {"container": n})
    lines = b.get("lines") or []
    if atype == "T" and lines:
        ctx.warn(P, "A Temporary Deposit does not need HTS lines (FT50/FT51)")
    if atype != "T" and not lines and action in ("A", "R", "S"):
        ctx.err(P, "At least one HTS line (FT50 + FT51) is required")
    check_count(ctx, P, "HTS lines", len(lines), LIMITS["lines"])
    build_lines(ctx, P, lines, action)


def build_lines(ctx, P, lines, action):
    prev_no, counts, open_set = 0, {}, None
    status_change = action == "S"
    for li, ln in enumerate(lines, 1):
        L = f"{P} › Line {li}"
        sec = up(ln.get("spi_secondary"))
        if sec and sec not in SPI_SECONDARY:
            ctx.err(L, "SPI Secondary must be X, V, F, G, H or M")
        raw_no = ln.get("line_no")
        if blank(raw_no):
            no = prev_no if sec == "V" and prev_no else prev_no + 1
        else:
            d = to_decimal(raw_no)
            no = int(d) if d is not None and d == d.to_integral_value() and d > 0 else None
            if no is None:
                ctx.err(L, "Line Item Number must be a whole number above zero")
                no = prev_no + 1
        if li == 1 and no != 1 and action == "A":
            ctx.err(L, "The first HTS line of a bill must be numbered 00001")
        if sec == "V":
            if no != prev_no:
                ctx.err(L, "An Article Set component (V) must repeat the line number of its set header (X)")
        elif no < prev_no:
            ctx.err(L, f"Line numbers must ascend within a bill (got {no} after {prev_no})")
        elif action == "A" and no > prev_no + 1:
            ctx.warn(L, f"Line {no} skips {prev_no + 1}: gaps are only expected on a Replace that removed a line")
        counts[no] = counts.get(no, 0) + 1
        if counts[no] > 8 and not sec:
            ctx.err(L, f"Line number {no} is used more than 8 times (only Article Sets may repeat it without limit)")
        hts = re.sub(r"[.\s]", "", s(ln.get("htsus")))
        if not re.fullmatch(r"\d{10}", hts):
            ctx.err(L, "HTSUS must be a 10-digit number (e.g. 8518.22.0000)")
        coo = up(ln.get("coo"))
        if coo == "CA":
            ctx.err(L, "Canada cannot be reported as CA: use the Census province code (XA, XB, XC, XM, XN, XO, XP, XQ, XS, XT, XV, XW or XY)")
        elif coo and not (re.fullmatch(r"[A-Z]{2}", coo)):
            ctx.err(L, "Country of Origin must be a 2-letter ISO code")
        if sec != "V":
            close_set(ctx, P, open_set)
            open_set = None
        if sec == "X":
            open_set = {"header_line": li, "hts": hts, "header_value": None, "sum": 0, "n": 0}
        elif sec == "V":
            if open_set is None:
                ctx.err(L, "An Article Set component (V) must follow its header (X)")
            else:
                if open_set["n"] == 0 and hts != open_set["hts"]:
                    ctx.err(L, "The first Article Set component must carry the same HTS number as the set header")
                open_set["n"] += 1
        qty1, qty2 = ln.get("qty1"), ln.get("qty2")
        v50 = dict(line_no=no, htsus=hts, spi=ln.get("spi"), spi_country=ln.get("spi_country"), spi_secondary=sec, coo=coo,
                   qty1=0 if status_change else qty1, uom1="" if status_change else ln.get("uom1"),
                   qty2=0 if status_change else qty2, uom2="" if status_change else ln.get("uom2"),
                   quota=ln.get("quota"), pn_disclaimer=ln.get("pn_disclaimer"))
        if not status_change and blank(qty1):
            ctx.err(L, "Quantity 1 is required")
        if not status_change and blank(ln.get("uom1")):
            ctx.err(L, "Unit of Measure 1 is required")
        if not blank(qty2) and blank(ln.get("uom2")) and not status_change:
            ctx.warn(L, "Quantity 2 was given without a Unit of Measure 2")
        add(ctx, "FT50", L, v50, need={"qty1"} if not status_change else set())
        value = whole(ln.get("value"), L, ctx, "Value") if not blank(ln.get("value")) else None
        charges = whole(ln.get("charges"), L, ctx, "Charges") if not blank(ln.get("charges")) else None
        weight = whole(ln.get("weight"), L, ctx, "Weight") if not blank(ln.get("weight")) else None
        zs = ZONE_STATUS.get(up(ln.get("zone_status")), up(ln.get("zone_status")))
        if zs and zs not in ("P", "N", "D", "Z"):
            ctx.err(L, "Zone Status must be P, N, D or Z")
        v51 = dict(weight=weight, value=value, charges=charges, zone_status=zs, hmf=0 if blank(ln.get("hmf")) else ln.get("hmf"))
        need51 = set()
        if status_change:
            ex, rq = up(ln.get("existing_status")), up(ln.get("requested_status"))
            if ex != "N":
                ctx.err(L, "Existing Zone Status must be N (only Non-privileged Foreign can be changed)")
            if rq not in ("P", "Z"):
                ctx.err(L, "Requested Zone Status must be P or Z")
            aq = to_decimal(ln.get("affected_qty"))
            if aq is None or aq <= 0 or aq != aq.to_integral_value():
                ctx.err(L, "Quantity to be Affected must be a whole number above zero")
            if blank(ln.get("inventory_uom")):
                ctx.err(L, "Inventory Unit of Measure is required for a Status Change")
            v51.update(existing_status=ex, requested_status=rq, affected_qty=ln.get("affected_qty"), inventory_uom=ln.get("inventory_uom"))
            need51 = {"existing_status", "requested_status", "affected_qty", "inventory_uom"}
        add(ctx, "FT51", L, v51, need=need51, blank_keys=set() if status_change else {"existing_status", "requested_status", "affected_qty", "inventory_uom"})
        if open_set is not None:
            if sec == "X":
                open_set["header_value"] = value
            elif sec == "V" and value is not None:
                open_set["sum"] += value
        prev_no = no
        if status_change:
            continue                                     # Status Change map has no FT60 / FT61
        refs = ln.get("refs") or []
        if not any(up(r.get("qualifier")) == "MID" and not blank(r.get("ref_id")) for r in refs):
            ctx.err(L, "An FT60 with Reference Qualifier MID and the Manufacturer Identification Code is required for every HTS line")
        check_count(ctx, L, "FT60 records", len(refs), LIMITS["refs"])
        for ri, r in enumerate(refs, 1):
            q, rid_ = up(r.get("qualifier")), s(r.get("ref_id"))
            if q and q not in QUALIFIERS:
                ctx.err(f"{L} › FT60 {ri}", "Reference Qualifier must be MID, STL, DIA or ALU")
            if q and blank(rid_):
                ctx.err(f"{L} › FT60 {ri}", "Reference ID is required when a qualifier is used")
            if q == "DIA":
                rid_ = diamond_ref(rid_, f"{L} › FT60 {ri}", ctx)
            add(ctx, "FT60", f"{L} › FT60 {ri}", dict(description=r.get("description"), qualifier=q, ref_id=rid_),
                need={"qualifier", "ref_id"} if (q or not blank(rid_)) else set())
        rem = [s(x) for x in (ln.get("remarks") or []) if s(x)]
        check_count(ctx, L, "FT61 remarks", len(rem), LIMITS["remarks61"])
        for ri, r in enumerate(rem, 1):
            add(ctx, "FT61", f"{L} › FT61 {ri}", {"remarks": r})
    close_set(ctx, P, open_set)


def close_set(ctx, P, st):
    if st and st["n"] == 0:
        ctx.err(f"{P} › Line {st['header_line']}", "An Article Set header (X) needs at least two components (V)")
    elif st and st["header_value"] is not None and st["header_value"] != st["sum"]:
        ctx.warn(f"{P} › Line {st['header_line']}", f"Article Set header value {st['header_value']} should equal the sum of its components ({st['sum']})")


def diamond_ref(v, path, ctx):
    """Kimberley Process certificate rules from the CBP Administrative Message quoted in the CATAIR (FT60, DIA)."""
    t = up(v)
    m = re.fullmatch(r"([A-Z]{2})([A-Z0-9]*)", t)
    if not m:
        ctx.err(path, "A diamond certificate starts with a 2-letter ISO country code")
        return t
    iso, rest = m.groups()
    if len(rest) > 7:
        extra = rest[:-7]
        if set(extra) != {"0"}:
            ctx.err(path, "A diamond certificate longer than 9 characters can only drop leading zeros to fit positions 51-59")
            return t
        rest = rest[-7:]
    return iso + rest.rjust(7, "0")


def finish(ctx, result, mapkey):
    result.update(ok=not ctx.errors, errors=ctx.errors, warnings=ctx.warnings, records=ctx.records, counts=ctx.counts, usage_map=mapkey)
    return result


# ----------------------------------------------------------------------------------------------- batch envelope + file
def build_envelope(abi, password, transmitted=None):
    """A and B records (before) and Y and Z records (after) for the block. Returns (head, tail, errors)."""
    ctx = Ctx()
    abi = abi or {}
    d = transmitted if transmitted is not None else date.today()
    stamp = d.strftime("%m%d%y")
    pw = up(password)
    if not pw:
        ctx.err("Batch envelope", "Enter the ABI communication password (6 characters); it is used for this download only and is never stored")
    vals = dict(site=abi.get("site_code"), sender=abi.get("sender_id"), password=pw, date=stamp, app=APP_ID, office=abi.get("office_code"),
                port=abi.get("port_code"), filer=abi.get("filer_code"))
    a = make_record(ctx, "A", "A record", vals, layout=ENVELOPE["A"])
    b = make_record(ctx, "B", "B record", vals, layout=ENVELOPE["B"])
    y = make_record(ctx, "Y", "Y record", vals, layout=ENVELOPE["Y"])
    z = make_record(ctx, "Z", "Z record", vals, layout=ENVELOPE["Z"])
    return [a, b], [y, z], ctx.errors


def render_file(body_lines, head=None, tail=None, eol="\n"):
    lines = list(head or []) + list(body_lines) + list(tail or [])
    assert all(len(x) == RECORD_LEN for x in lines)
    return eol.join(lines) + eol


def layout_summary():
    """Machine-readable layout for the UI and for tools/verify_catair.py."""
    out = {}
    for rid, spec in LAYOUT.items():
        out[rid] = {"title": spec["title"], "fields": [{k: v for k, v in f.items() if k in
                    ("key", "label", "start", "end", "len", "cls", "req", "help", "choices", "implied", "date", "derived")} for f in spec["fields"]]}
    return out
