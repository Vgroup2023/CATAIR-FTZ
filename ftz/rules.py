"""Pure domain rules and validators (no database access)."""
import re
from datetime import date, datetime, timedelta

ZONE_STATUSES = {
    "PF": "Privileged Foreign",
    "NPF": "Non-Privileged Foreign",
    "D": "Domestic",
    "ZR": "Zone-Restricted",
}
ACTIVITIES = {
    "manipulate": "Manipulate",
    "manufacture": "Manufacture",
    "exhibit": "Exhibit",
    "destroy": "Destroy",
    "temp_removal": "Temporary Removal",
}
PERMIT_KINDS = {"blanket": "Blanket (Annual)", "individual": "Individual / Specific"}
# CBP entry type codes for in-bond movements (CBP Form 7512).
INBOND_TYPES = {
    "IT": {"code": "61", "name": "Immediate Transportation"},
    "TE": {"code": "62", "name": "Transportation & Exportation"},
    "IE": {"code": "63", "name": "Immediate Exportation"},
}
MODES = ["truck", "rail", "air", "vessel", "other"]
PARTY_KINDS = ["operator", "importer", "consignee", "carrier", "broker", "surety"]

HTS_RE = re.compile(r"^\d{4}\.\d{2}\.\d{4}$")
DEFAULT_SETTINGS = {
    "lines_per_sheet": "10",
    # Placeholder transit windows: the real limit is set by CBP for each
    # port/mode, so operators must adjust these to their port's requirement.
    "transit_days_IT": "30",
    "transit_days_TE": "30",
    "transit_days_IE": "30",
    "expiry_warning_days": "30",
}


def parse_date(value):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def today():
    return date.today()


def add_days(iso, days):
    return (parse_date(iso) + timedelta(days=int(days))).isoformat()


def num(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_hts(value):
    """Accept 10 digits with or without dots and return NNNN.NN.NNNN."""
    digits = re.sub(r"[.\s]", "", str(value or ""))
    if len(digits) == 10 and digits.isdigit():
        return f"{digits[:4]}.{digits[4:6]}.{digits[6:]}"
    return str(value or "").strip()


def validate_line(line, idx, *, allow_domestic_blank_hts=True, require_rate=True):
    """Validate one merchandise line (shared by 214 and in-bond lines)."""
    errs = []
    p = f"Line {idx}: "
    status = (line.get("zone_status") or "").upper()
    if not str(line.get("description") or "").strip():
        errs.append(p + "description is required")
    if status not in ZONE_STATUSES:
        errs.append(p + "zone status must be one of PF, NPF, D, ZR")
    q = num(line.get("qty"))
    if q is None or q <= 0:
        errs.append(p + "quantity must be greater than 0")
    if not str(line.get("uom") or "").strip():
        errs.append(p + "unit of measure is required")
    v = num(line.get("value"))
    if v is None or v < 0:
        errs.append(p + "value must be 0 or more")
    hts = normalize_hts(line.get("htsus"))
    foreign = status in ("PF", "NPF")
    if hts and not HTS_RE.match(hts):
        errs.append(p + "HTSUS must be 10 digits (NNNN.NN.NNNN)")
    elif not hts and not (status == "D" and allow_domestic_blank_hts) and status in ZONE_STATUSES:
        errs.append(p + "HTSUS number is required")
    if foreign and not str(line.get("coo") or "").strip():
        errs.append(p + "country of origin is required for foreign merchandise")
    if status == "PF" and require_rate:
        rate = num(line.get("duty_rate"))
        if rate is None or rate < 0 or rate > 100:
            errs.append(p + "privileged foreign status locks the duty rate: enter 0-100 (%)")
    return errs


def validate_admission(header, lines):
    errs = []
    for f, label in [
        ("zone_id", "zone"), ("operator_id", "zone operator"), ("carrier_id", "carrier"),
        ("transport_doc", "bill of lading / AWB"), ("port_of_entry", "port of entry"),
        ("entry_date", "admission date"),
    ]:
        if not header.get(f):
            errs.append(f"Header: {label} is required")
    if header.get("entry_date") and not parse_date(header["entry_date"]):
        errs.append("Header: admission date must be YYYY-MM-DD")
    if not lines:
        errs.append("At least one line is required")
    for i, ln in enumerate(lines, 1):
        errs += validate_line(ln, i)
    return errs


def sheet_layout(lines, per_sheet, census):
    """Split lines across the base form and continuation sheets.

    214 -> 214B when not reporting Census statistics; 214A -> 214C when doing so.
    """
    per_sheet = max(1, int(per_sheet))
    base, cont = ("214A", "214C") if census else ("214", "214B")
    sheets = []
    for n, start in enumerate(range(0, max(len(lines), 1), per_sheet)):
        sheets.append({"form": base if n == 0 else cont, "sheet": n + 1,
                       "lines": lines[start:start + per_sheet]})
    for s in sheets:
        s["of"] = len(sheets)
    return sheets


def permit_covers(permit, activity, on_date, zone_id):
    """Return (ok, reason) for whether a 216 permit allows an activity."""
    if permit["status"] != "active":
        return False, f"permit {permit['permit_no']} is {permit['status']}, not active"
    if permit["zone_id"] != zone_id:
        return False, "permit is for a different zone"
    d = parse_date(on_date)
    if not d:
        return False, "activity date must be YYYY-MM-DD"
    if not (parse_date(permit["valid_from"]) <= d <= parse_date(permit["valid_to"])):
        return False, f"{on_date} is outside the permit validity window"
    if activity not in permit["activity_list"]:
        return False, f"permit does not cover '{ACTIVITIES[activity]}'"
    return True, ""


def validate_permit(p):
    errs = []
    for f, label in [("zone_id", "zone"), ("operator_id", "operator"), ("valid_from", "valid from"),
                     ("valid_to", "valid to")]:
        if not p.get(f):
            errs.append(f"{label} is required")
    if p.get("kind") not in PERMIT_KINDS:
        errs.append("permit type must be blanket or individual")
    acts = p.get("activities") or []
    if not acts or any(a not in ACTIVITIES for a in acts):
        errs.append("select at least one valid activity")
    a, b = parse_date(p.get("valid_from")), parse_date(p.get("valid_to"))
    if p.get("valid_from") and not a or p.get("valid_to") and not b:
        errs.append("dates must be YYYY-MM-DD")
    elif a and b:
        if b < a:
            errs.append("valid to must not be before valid from")
        elif p.get("kind") == "blanket" and (b - a).days > 366:
            errs.append("a blanket permit covers at most 12 months")
    return errs


def validate_inbond(h, lines):
    errs = []
    t = h.get("type")
    if t not in INBOND_TYPES:
        errs.append("in-bond type must be IT, TE or IE")
    for f, label in [("carrier_id", "carrier"), ("origin_port", "origin port"),
                     ("dest_port", "destination port"), ("issued_date", "issue date"),
                     ("bl_no", "bill of lading / AWB")]:
        if not h.get(f):
            errs.append(f"{label} is required")
    if h.get("mode") not in MODES:
        errs.append("mode of transport is required")
    if h.get("issued_date") and not parse_date(h["issued_date"]):
        errs.append("issue date must be YYYY-MM-DD")
    o, d = str(h.get("origin_port") or "").strip(), str(h.get("dest_port") or "").strip()
    if t == "IE" and o and d and o != d:
        errs.append("IE (immediate exportation) is exported from the port of arrival: origin and destination ports must match")
    if t in ("IT", "TE") and o and d and o == d:
        errs.append(f"{t} moves merchandise to a different port: origin and destination must differ")
    if not lines:
        errs.append("at least one line is required")
    for i, ln in enumerate(lines, 1):
        errs += validate_line(ln, i, require_rate=False)
        if t == "IT" and (ln.get("zone_status") or "").upper() == "ZR":
            errs.append(f"Line {i}: zone-restricted merchandise cannot move in-bond for consumption entry (IT); use TE or IE")
    return errs


def estimate_duty(lot, qty, rate_override=None):
    """Duty estimate for consumption withdrawal.

    PF uses the rate locked at admission; NPF is classified and rated at withdrawal,
    so a rate must be supplied; D carries no duty.
    """
    value = qty * lot["unit_value"]
    if lot["zone_status"] == "D":
        return 0.0, None
    if lot["zone_status"] == "PF":
        rate = lot["duty_rate"]
    else:
        rate = rate_override
        if rate is None:
            return None, "NPF merchandise is rated at withdrawal: enter the duty rate in force"
    return round(value * rate / 100.0, 2), None
