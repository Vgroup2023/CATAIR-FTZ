"""CATAIR FZ (Input) builder and NF / ZD (Output) reader for the Foreign Trade Zone chapter.

Source: "ACE CATAIR Foreign Trade Zone Admission (e214)", Version 3.1.3, August 2026 (see catair.SOURCE).
  * FZ  = the zone operator's follow-up messages on an admission: concurrence of received quantities, Permit To Transfer
          (PTT) request / cancel, operator acceptance or refusal, physical arrival, and Post Admission Correction request.
  * NF  = CBP's response to an FT or FZ transmission (NF90, NF91, NF92, NF95).
  * ZD  = CBP's "FTZ output for broker download": a copy of the admission, laid out like the FT input records.
Like catair.py this only prepares and reads 80-character records; it never transmits anything.
"""
import re

from . import catair
from .catair import RECORD_LEN, Ctx, _ctl, _f, _fill, add, blank, ccyymmdd, check_count, finish, ior_check, s, up

# ----------------------------------------------------------------------------------------------- FZ input layouts
FZ_LAYOUT = {
    "FZ10": {"title": "Action set-up", "fields": [
        _ctl(10),
        _f("qualifier", "Action Qualifier", 3, 3, "N", "M", choices=["1", "2", "3", "4"],
           help="1 Admission number · 2 Bill of lading · 3 In-bond number · 4 Container (the bill of lading goes in the next field)"),
        _f("id_number", "Identification Number", 4, 38, "AN", "M", help="Min 9, max 35. Admission, bill of lading, in-bond number, as the qualifier says. A house bill is sent as-is, no leading zeros"),
        _f("action", "Action Code", 39, 40, "A", "M", choices=list("ABCDFGHIJK"), help="Left-justified, one letter (see the action list)"),
        _f("received_qty", "Received Quantity", 41, 50, "N", "C", help="Whole number. Concurrence (A-D), PTT request (F), or a partial arrival (I)"),
        _f("delivery_code", "Delivery Code", 51, 52, "A", "C", choices=["PA", "FI"], help="PA partial · FI final. Required for concurrence (A-D)"),
        _f("irs", "IRS Identifier (Bonded Carrier)", 53, 64, "X", "C", help="Importer of Record number of the bonded carrier with an active type 2 bond. Required for a PTT request (F)"),
        _f("firms", "FIRMS (destination site)", 65, 68, "AN", "C", help="FIRMS code of the FTZ site. Required for a PTT request (F) and for arrival (I), except an air in-bond arrival"),
        _f("airport", "Airport Code", 69, 71, "A", "C", help="IATA 3-letter code. Required when the bill of lading arrived by air"),
        _fill(72, 80)]},
    "FZ11": {"title": "Container / split-shipment detail", "fields": [
        _ctl(11),
        _f("container", "Container Number", 3, 17, "AN", "C", help="4-letter equipment prefix + number. Required for a PTT at container level"),
        _f("admission_number", "Admission Number", 18, 37, "AN", "C", help="Required when the qualifier is 2, 3 or 4, unless the PTT is for a Direct Delivery"),
        _f("carrier_code", "Airline Carrier Code of Importing Carrier", 38, 41, "AN", "C", help="Air waybills only"),
        _f("flight", "Flight Number", 42, 56, "AN", "C", help="Air waybills only"),
        _f("sched_arrival", "Scheduled Date of Arrival", 57, 64, "N", "C", date=True, help="CCYYMMDD. Air waybills only"),
        _f("container_qty", "Container Manifested Quantity", 65, 79, "N", "C", help="Whole number. Required for a PTT at container level"),
        _fill(80, 80)]},
    "FZ12": {"title": "Reference (PID)", "fields": [
        _f("_id", "Control Identifier", 1, 2, "AN", "M", fixed="12"),
        _f("qualifier", "Reference Identifier Qualifier", 3, 5, "AN", "M", help="PID = PTT Unique Identifier"),
        _f("ref_id", "Reference Identifier", 6, 55, "X", "M", help="The PID returned by CBP when the PTT was accepted (max 19)"),
        _fill(56, 80)]},
    "FZ13": {"title": "Post Admission Correction request", "fields": [
        _ctl(13),
        _f("contact_name", "Contact Name", 3, 42, "AN", "M"),
        _f("contact_phone", "Contact Phone", 43, 57, "N", "M", pack="left", help="Digits only"),
        _f("reason", "Reason Code", 58, 59, "N", "M", help="01-11, see the reason list"),
        _fill(60, 80)]},
    "FZ14": {"title": "Correction reference", "fields": [
        _ctl(14),
        _f("qualifier", "Reference Qualifier", 3, 5, "AN", "M", choices=["EN", "IB", "FTZ"], help="EN replacement entry · IB replacement in-bond · FTZ replacement admission"),
        _f("ref_id", "Reference ID", 6, 55, "X", "M"),
        _fill(56, 80)]},
    "FZ20": {"title": "Remarks", "fields": [
        _ctl(20),
        _f("remarks", "Remarks", 3, 32, "AN", "M"),
        _f("remarks2", "Remarks (continued)", 33, 62, "AN", "O"),
        _fill(63, 80)]},
}

FZ_ACTIONS = {
    "A": ("Concur: all goods on the admission", ["1"]), "B": ("Concur: goods on one bill of lading", ["2"]),
    "C": ("Concur: goods under one in-bond number", ["3"]), "D": ("Concur: goods in a container", ["4"]),
    "F": ("Permit To Transfer (request)", ["2", "4"]), "G": ("Operator refusal: goods not acceptable", ["1"]),
    "H": ("Operator acceptance: goods acceptable", ["1"]), "I": ("Report physical arrival at the zone", ["2", "3", "4"]),
    "J": ("Post Admission Correction request", ["1"]), "K": ("Permit To Transfer (cancel)", ["2"])}
PAC_REASONS = {"01": "Clerical error", "02": "Admission replaced by CBP Form 7512", "03": "Merchandise cleared under another admission",
               "04": "Admission replaced by a formal entry", "05": "Merchandise cleared under informal entry", "06": "Merchandise seized",
               "07": "Merchandise destroyed", "08": "Non-arrival", "09": "Shipment refused by importer/zone operator",
               "10": "Shipment not authorized for import (refused by PGA)", "11": "System error"}
PAC_NEEDS_REF = {"02", "03", "04", "05"}
FZ_MAP = ("Input Records Usage Map (FZ)", [
    ("A, B", "Transaction Control Headers", "M", "1", "", 0),
    ("FZ10", "Action set-up: PTT, operator acceptance/refusal, arrival, concurrence", "M", "1", "999", 1),
    ("FZ11", "Container information / split-shipment manifest", "C", "1", "", 2),
    ("FZ12", "Reference identifier (the PID for a PTT cancel)", "C", "1", "", 2),
    ("FZ13", "Post Admission Correction request contact and reason", "C", "1", "1", 2),
    ("FZ14", "Replacement entry / in-bond / admission number", "C", "1", "", 3),
    ("FZ20", "Open text comments", "O", "10", "", 2),
    ("Y, Z", "Transaction Control Trailers", "M", "1", "", 0)])
FZ_LIMITS = {"actions": 999, "remarks": 10}


# ----------------------------------------------------------------------------------------------- FZ compile
def compile_fz(data):
    """Validate and build FZ (Input) records. data = {"actions": [{...}]}. Never raises on bad data."""
    ctx = Ctx()
    acts = (data or {}).get("actions") if isinstance(data, dict) else None
    acts = acts if isinstance(acts, list) else []
    if not acts:
        ctx.err("Transaction", "Add at least one action")
    check_count(ctx, "Transaction", "actions", len(acts), FZ_LIMITS["actions"])
    first = ""
    for i, a in enumerate(acts, 1):
        a = a if isinstance(a, dict) else {}
        P = f"Action {i}"
        code, q = up(a.get("action")), s(a.get("qualifier"))
        if code == "E":
            ctx.err(P, "Action Code E is not used")
        elif code not in FZ_ACTIONS:
            ctx.err(P, "Action Code must be one of " + ", ".join(sorted(FZ_ACTIONS)))
            code = ""
        elif q not in FZ_ACTIONS[code][1]:
            ctx.err(P, f"Action {code} ({FZ_ACTIONS[code][0]}) needs Action Qualifier " + " or ".join(FZ_ACTIONS[code][1]))
        ident = up(a.get("id_number"))
        if ident and q != "4" and len(ident) < 9 and not a.get("house_bill"):
            ctx.warn(P, "Identification Number is shorter than the 9-character minimum; tick 'house bill' only if it really is one")
        if q == "1" and ident and not re.fullmatch(r"[A-Z0-9]{9,19}", ident):
            ctx.warn(P, "An admission number is the Zone ID + year + control number (9 to 19 letters or numbers)")
        direct = bool(a.get("direct_delivery"))
        air = bool(a.get("air"))
        conts = [c for c in (a.get("containers") or []) if isinstance(c, dict) and (up(c.get("container")) or not blank(c.get("qty")))]
        qty = a.get("received_qty")
        if code == "F" and q == "4" and blank(qty) and conts and all(not blank(c.get("qty")) for c in conts):
            try:
                qty = sum(int(float(c["qty"])) for c in conts)
                ctx.warn(P, f"Received Quantity was set to {qty}, the total of the container quantities (CATAIR's field note requires it for a PTT request)")
            except (TypeError, ValueError):
                pass
        need10 = []
        if code in ("A", "B", "C", "D", "F"):
            need10.append("received_qty")
        if code in ("A", "B", "C", "D"):
            need10.append("delivery_code")
        if code in ("A", "C") and up(a.get("delivery_code")) not in ("", "FI"):
            ctx.err(P, f"Action {code} does not accept partial quantities: Delivery Code must be FI")
        if code == "F":
            need10 += ["irs", "firms"]
        if code == "I" and not (q == "3" and air):
            need10.append("firms")
        if air and code in ("A", "B", "C", "D", "F", "I", "K"):
            need10.append("airport")
        if code == "F" and q == "4" and not conts:
            ctx.err(P, "A container-level PTT needs at least one container (FZ11) with its manifested quantity")
        v10 = dict(qualifier=q, id_number=ident, action=code, received_qty=qty, delivery_code=up(a.get("delivery_code")), irs=up(a.get("irs")),
                   firms=up(a.get("firms")), airport=up(a.get("airport")))
        ior_check(ctx, P, "IRS Identifier", v10["irs"])
        if not ident:
            ctx.err(P, "Identification Number is required")
        first = first or ident
        add(ctx, "FZ10", P, v10, need=need10, layout=FZ_LAYOUT["FZ10"]["fields"])
        # FZ11 / FZ12
        sets = []
        if q == "4" or conts:
            for c in conts or [{}]:
                sets.append(dict(container=up(c.get("container")), container_qty=c.get("qty")))
        elif code in ("F", "K", "I", "A", "B", "C", "D") and (q in ("2", "3") and (not direct or air)):
            sets.append({})
        for ci, cs in enumerate(sets, 1):
            Pc = P if len(sets) == 1 else f"{P} container {ci}"
            v11 = dict(cs, admission_number=up(a.get("admission_number")), carrier_code=up(a.get("carrier_code")), flight=up(a.get("flight")),
                       sched_arrival=ccyymmdd(a.get("sched_arrival"), Pc, ctx, "Scheduled Date of Arrival"))
            n11 = []
            if q in ("2", "3", "4") and not direct and code != "G":
                n11.append("admission_number")
            if q == "4":
                n11.append("container")
            if code == "F" and q == "4":
                n11.append("container_qty")
            if air:
                n11 += ["carrier_code", "flight", "sched_arrival"]
            if any(not blank(v) for v in v11.values()) or n11:
                add(ctx, "FZ11", Pc, v11, need=n11, layout=FZ_LAYOUT["FZ11"]["fields"])
        if code == "K":
            pid = up(a.get("pid"))
            if not pid:
                ctx.err(P, "A PTT cancel needs the PID that CBP returned when the PTT was accepted")
            elif len(pid) > 19:
                ctx.err(P, "A PID is at most 19 characters")
            if not sets and (not direct or up(a.get("admission_number"))):
                add(ctx, "FZ11", P, dict(admission_number=up(a.get("admission_number"))), need=[] if direct else ["admission_number"],
                    layout=FZ_LAYOUT["FZ11"]["fields"])
            add(ctx, "FZ12", P, dict(qualifier="PID", ref_id=pid), layout=FZ_LAYOUT["FZ12"]["fields"])
        if code == "J":
            pc = a.get("correction") or {}
            reason = s(pc.get("reason")).zfill(2) if s(pc.get("reason")) else ""
            if reason and reason not in PAC_REASONS:
                ctx.err(P, "Correction reason must be 01 to 11")
            add(ctx, "FZ13", P, dict(contact_name=pc.get("contact_name"), contact_phone=re.sub(r"\D", "", s(pc.get("contact_phone"))), reason=reason),
                layout=FZ_LAYOUT["FZ13"]["fields"])
            ref = pc.get("ref") or {}
            if reason in PAC_NEEDS_REF or not blank(ref.get("ref_id")):
                if reason in PAC_NEEDS_REF and blank(ref.get("ref_id")):
                    ctx.err(P, f"Reason {reason} ({PAC_REASONS[reason]}) needs the replacement number (FZ14)")
                add(ctx, "FZ14", P, dict(qualifier=up(ref.get("qualifier")), ref_id=up(ref.get("ref_id"))), layout=FZ_LAYOUT["FZ14"]["fields"])
        rem = [s(x) for x in (a.get("remarks") or []) if s(x)]
        check_count(ctx, P + " remarks", "FZ20 records", len(rem), FZ_LIMITS["remarks"])
        for r in rem[:FZ_LIMITS["remarks"]]:
            t, changed = catair.clean_text(r, 60)
            if changed:
                ctx.warn(P, "Remarks were made upper case with symbols removed, as CATAIR allows letters and numbers only")
            add(ctx, "FZ20", P, dict(remarks=t[:30], remarks2=t[30:60]), layout=FZ_LAYOUT["FZ20"]["fields"])
    res = {"admission_number": first, "action": ",".join(sorted({up(x.get("action")) for x in acts if isinstance(x, dict) and up(x.get("action"))}))}
    return finish(ctx, res, "FZ")


# ----------------------------------------------------------------------------------------------- NF / ZD output
NF_LAYOUT = {
    "NF90": [_ctl(90), _f("admission_type", "Admission Type", 3, 3, "A", "C"), _f("zone_id", "Zone ID", 4, 12, "AN", "C"),
             _f("calendar_year", "Calendar Year", 13, 14, "N", "M"), _f("control_number", "Control Number", 15, 22, "AN", "C"),
             _f("port_code", "Port Code", 23, 26, "N", "M"), _f("direct_delivery", "Direct Delivery Indicator", 27, 27, "A", "C"), _fill(28, 80)],
    "NF91": [_f("_id", "Record Type", 1, 2, "N", "M", fixed="91"), _f("disposition", "Disposition Code", 3, 5, "AN", "M"),
             _f("ref_qualifier", "Reference Qualifier", 6, 8, "AN", "M"), _f("ref_id", "Reference ID", 9, 43, "AN", "M"),
             _f("action_date", "Action Date", 44, 49, "N", "M"), _f("action_time", "Action Time", 50, 53, "N", "M"),
             _f("carrier_code", "In-bond Carrier Code", 54, 57, "X", "O"), _f("firms", "FIRMS ID", 58, 61, "AN", "C"),
             _f("container", "Container Number", 62, 76, "AN", "O"), _fill(77, 80)],
    "NF92": [_ctl(92), _f("qualifier", "Reference Identifier Qualifier", 3, 5, "AN", "M"), _f("ref_id", "Reference Identifier", 6, 50, "X", "M"),
             _f("carrier_code", "Airline Carrier Code of Importing Carrier", 51, 54, "AN", "C"), _f("flight", "Flight Number", 55, 69, "AN", "C"),
             _f("sched_arrival", "Scheduled Date of Arrival", 70, 77, "N", "C"), _fill(78, 80)],
    "NF95": [_f("_id", "Record Type", 1, 2, "N", "M", fixed="95"), _f("message_type", "Narrative Message Type Code", 3, 4, "N", "C"),
             _f("message_code", "Error Code (Message Code)", 5, 7, "X", "C"), _f("remarks", "Remarks", 8, 67, "X", "M"), _fill(68, 80)],
}
NF_IDS = {"90": "NF90", "91": "NF91", "92": "NF92", "95": "NF95"}
NARRATIVE = {"01": "Data rejection (error)", "02": "Data acceptance", "03": "Data acceptance with warning"}
REF_QUALIFIERS = {"1": "FTZ admission number", "2": "Bill of lading", "3": "In-bond number", "4": "Container number"}

# Scenario table from the NF "usage" pages: (NF91 disposition, NF95 type, NF95 code) -> (what happened, outcome, CBP text)
# outcome: accepted | warning | rejected | authorized | info
NF_SCENARIOS = {
    ("B1", "", "083"): ("FT admission data accepted (Add or Replace)", "accepted"),
    ("B1", "02", "084"): ("FT admission deleted", "accepted"),
    ("B1", "02", "214"): ("FT zone status change accepted", "accepted"),
    ("B2", "03", ""): ("FT accepted with Census warnings", "warning"),
    ("B3", "01", "082"): ("FT admission data rejected", "rejected"),
    ("B4", "", ""): ("Paperless admission (selectivity)", "authorized"),
    ("B5", "", ""): ("Admission documents required (selectivity)", "warning"),
    ("BF", "", ""): ("Admission authorized", "authorized"),
    ("BH", "", ""): ("Admission deleted (bucket 2)", "accepted"),
    ("RAP", "", ""): ("Replace approved by CBP", "accepted"),
    ("RDE", "", ""): ("Replace denied by CBP", "rejected"),
    ("B6", "02", "115"): ("FZ concurrence data accepted", "accepted"),
    ("B6", "02", "116"): ("FZ Permit To Transfer data accepted", "accepted"),
    ("B6", "02", "117"): ("FZ operator refusal accepted", "accepted"),
    ("B6", "02", "118"): ("FZ operator acceptance accepted", "accepted"),
    ("B6", "02", "119"): ("FZ physical arrival accepted", "accepted"),
    ("B6", "02", "227"): ("Post Admission Correction request accepted", "accepted"),
    ("B6", "02", "224"): ("Permit To Transfer cancel accepted", "accepted"),
    ("B7", "01", "120"): ("FZ transaction data rejected", "rejected"),
    ("B7", "01", "215"): ("PTT cancel not authorized", "rejected"),
    ("JAP", "", ""): ("Post Admission Correction approved by CBP", "accepted"),
    ("JDE", "", ""): ("Post Admission Correction denied by CBP", "rejected"),
}
DISPOSITION_ONLY = {k[0]: v for k, v in NF_SCENARIOS.items()}
# ZD records follow the FT layouts, except where the chapter's ZD tables differ: ZD20 carries the scheduled arrival at 70-77,
# ZD50 has no PN Disclaimer Flag, ZD10's expanded-indicator is 1A and ZD51's zone status is 1AN.
ZD_IDS = {"10": "FT10", "11": "FT11", "12": "FT12", "20": "FT20", "40": "FT40", "41": "FT41", "42": "FT42", "43": "FT43",
          "50": "FT50", "51": "FT51", "60": "FT60", "61": "FT61"}


def _zd_layout(ft_id):
    fields = [dict(f) for f in catair.LAYOUT[ft_id]["fields"]]
    if ft_id == "FT10":
        for f in fields:
            if f["key"] == "expanded":
                f["cls"] = "A"
    elif ft_id == "FT20":
        fields = [f for f in fields if f["key"] not in ("scheduled_arrival", None) or f["start"] < 68]
        fields += [_f("scheduled_arrival", "Scheduled Date of Arrival", 70, 77, "N", "C"), _fill(78, 80)]
    elif ft_id == "FT50":
        fields = [f for f in fields if f["key"] not in ("pn_disclaimer", None)] + [_fill(57, 80)]
    elif ft_id == "FT51":
        for f in fields:
            if f["key"] == "zone_status":
                f["cls"] = "AN"
    return fields


ZD_LAYOUT = {"ZD" + k: _zd_layout(v) for k, v in ZD_IDS.items()}


def _slice(line, f):
    return line[f["start"] - 1:f["end"]]


def decode_line(line, layout, rid):
    fields = []
    for f in layout:
        if f["key"] is None or f["cls"] == "S":
            continue
        fields.append({"label": f["label"], "start": f["start"], "end": f["end"], "value": _slice(line, f).strip()})
    return {"id": rid, "text": line, "fields": fields}


def parse_output(text, kind="auto"):
    """Decode a CBP response or broker-download file. kind: 'nf' (response), 'zd' (admission copy) or 'auto'.
    Returns {kind, records, results[], problems[]}. Lines of the wrong length are reported, never guessed at."""
    problems, lines = [], []
    for n, raw in enumerate(str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        if not raw.strip():
            continue
        line = raw.rstrip("\n")
        if len(line) > RECORD_LEN and not line[RECORD_LEN:].strip():
            line = line[:RECORD_LEN]
        if len(line) < RECORD_LEN:
            if len(line.rstrip()) <= RECORD_LEN:       # trailing blanks are often trimmed by mail/FTP tools
                line = line.ljust(RECORD_LEN)
        if len(line) != RECORD_LEN:
            problems.append(f"Line {n}: {len(raw)} characters; CATAIR records are exactly {RECORD_LEN}")
            continue
        lines.append((n, line))
    ids = [l[0:2] for _, l in lines]
    if kind == "auto":
        kind = "nf" if any(i in NF_IDS for i in ids) else "zd"
    records, results, cur = [], [], None
    for n, line in lines:
        cid = line[0:2]
        if line[0] in "ABYZ" and not line[0:2].isdigit():
            records.append({"id": line[0], "text": line, "fields": [], "note": "envelope record"})
            continue
        if kind == "nf" and cid in NF_IDS:
            rid = NF_IDS[cid]
            rec = decode_line(line, NF_LAYOUT[rid], rid)
            records.append(rec)
            v = {f["label"]: f["value"] for f in rec["fields"]}
            if rid == "NF90":
                cur = {"admission_number": (v["Zone ID"] + v["Calendar Year"] + v["Control Number"]).strip(), "port_code": v["Port Code"],
                       "refs": [], "messages": [], "disposition": "", "echoed": []}
                results.append(cur)
            elif cur is None:           # CBP's own examples show NF91/NF95 on their own: keep them as an unnamed response
                cur = {"admission_number": "", "port_code": "", "refs": [], "messages": [], "disposition": "", "echoed": []}
                results.append(cur)
            if rid == "NF91":
                cur["disposition"] = cur["disposition"] or v["Disposition Code"]
                cur["refs"].append({"qualifier": REF_QUALIFIERS.get(v["Reference Qualifier"], v["Reference Qualifier"]), "id": v["Reference ID"],
                                    "date": _yymmdd(v["Action Date"]), "time": _hhmm(v["Action Time"]), "firms": v["FIRMS ID"], "container": v["Container Number"],
                                    "disposition": v["Disposition Code"]})
            if rid == "NF92":
                cur["refs"].append({"qualifier": v["Reference Identifier Qualifier"], "id": v["Reference Identifier"], "flight": v["Flight Number"],
                                    "carrier": v["Airline Carrier Code of Importing Carrier"], "date": v["Scheduled Date of Arrival"]})
            if rid == "NF95":
                cur["messages"].append({"type": v["Narrative Message Type Code"], "type_text": NARRATIVE.get(v["Narrative Message Type Code"], ""),
                                        "code": v["Error Code (Message Code)"], "text": v["Remarks"]})
        elif kind == "zd" and cid in ZD_IDS:
            rid = ZD_IDS[cid]
            records.append(decode_line(line, ZD_LAYOUT["ZD" + cid], "ZD" + cid))
        else:
            records.append({"id": "??" if not cid.isdigit() else cid, "text": line, "fields": [], "note": "echoed input record or unknown: shown as sent"})
            if cur is not None and kind == "nf":
                cur["echoed"].append(line.rstrip())
    for r in results:
        r["outcome"], r["summary"] = classify(r)
    if kind == "nf" and not results:
        problems.append("No NF90 record found: this does not look like a CBP response")
    return {"kind": kind, "records": records, "results": results, "problems": problems}


def _yymmdd(t):
    return f"20{t[0:2]}-{t[2:4]}-{t[4:6]}" if re.fullmatch(r"\d{6}", t or "") else t


def _hhmm(t):
    return f"{t[0:2]}:{t[2:4]}" if re.fullmatch(r"\d{4}", t or "") else t


def classify(res):
    """Return (outcome, plain-language summary) for one decoded response."""
    disp = res.get("disposition") or ""
    msgs = res.get("messages") or []
    mtype = msgs[0]["type"] if msgs else ""
    code = msgs[0]["code"] if msgs else ""
    hit = NF_SCENARIOS.get((disp, mtype, code)) or NF_SCENARIOS.get((disp, "", "")) or DISPOSITION_ONLY.get(disp)
    if mtype == "01" and (not hit or hit[1] not in ("rejected",)):
        hit = ("Rejected by CBP", "rejected")
    if hit:
        text = hit[0]
        if msgs and msgs[0]["text"]:
            text += ": " + msgs[0]["text"]
        return hit[1], text
    if msgs:
        return {"01": "rejected", "02": "accepted", "03": "warning"}.get(mtype, "info"), msgs[0]["text"] or "CBP message"
    return "info", f"Disposition {disp}" if disp else "No disposition found"
