#!/usr/bin/env python3
"""Check ftz/catair.py's record layouts against the official CBP CATAIR PDFs.

    pip install pymupdf
    python3 tools/verify_catair.py FTZ_CATAIR.pdf [ABI_Batch_and_Block_Control.pdf]

Compares, for every field of FT10-FT61 (and A/B/Y/Z when the second PDF is given): start-end position, length, class and
M/C/O designation. Exits non-zero on any difference, so run it whenever CBP publishes a new CATAIR version.
Treats the PDFs as untrusted input: it only reads text and table cells.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

try:
    import pymupdf
except ImportError:                                   # older package name
    import fitz as pymupdf

from ftz import catair

# A few CBP cells hold alternatives ("6D or 6S", or "32S 5N 27S" for ESAR vs eMAN): the first one is the ESAR layout FT uses.
CLASS = re.compile(r"^(\d+)\s*([A-Z]+)\b")
POS = re.compile(r"^(\d+)(?:\s*-\s*(\d+))?\b")


def cells(row):
    return [re.sub(r"\s+", " ", (c or "").replace("\n", " ")).strip() for c in row if (c or "").strip()]


def parse_tables(pdf, title_re, rows_ok):
    """Yield (record id, [(name, len, cls, start, end, designation)]) from every field table in the PDF."""
    found = {}
    for page in pymupdf.open(pdf):
        for t in page.find_tables().tables:
            rows = [cells(r) for r in t.extract()]
            rid = None
            for r in rows:
                m = title_re.search(" ".join(r))
                if m:
                    rid = m.group(1)
                    break
            if not rid:
                continue
            for r in rows:
                for i, c in enumerate(r):
                    m = CLASS.match(c)
                    if m and i + 2 < len(r) + 1 and i + 1 < len(r) and POS.match(r[i + 1]):
                        pm = POS.match(r[i + 1])
                        start = int(pm.group(1))
                        end = int(pm.group(2) or pm.group(1))
                        status = (r[i + 2] if i + 2 < len(r) else "").split()[0] if i + 2 < len(r) else ""
                        found.setdefault(rid, []).append((r[0] if i else "", int(m.group(1)), m.group(2), start, end, status))
                        break
    return found


def compare(rid, pdf_fields, mine):
    problems = []
    by_pos = {(f["start"], f["end"]): f for f in mine}
    seen = set()
    for name, ln, cls, start, end, status in pdf_fields:
        f = by_pos.get((start, end))
        if not f:
            problems.append(f"{rid}: PDF has '{name}' at {start}-{end} ({ln}{cls}, {status}) that the layout lacks")
            continue
        seen.add((start, end))
        if f["len"] != ln or f["cls"] != cls:
            problems.append(f"{rid} '{f['label']}' {start}-{end}: layout {f['len']}{f['cls']} but PDF says {ln}{cls}")
        # Multi-part cells like "M- ESAR OR O- eMAN" are reported as differences only for the plain M/C/O case.
        if status in ("M", "C", "O") and f["req"] != status:
            problems.append(f"{rid} '{f['label']}' {start}-{end}: layout designation {f['req']} but PDF says {status}")
    for key, f in by_pos.items():
        if key not in seen:
            problems.append(f"{rid}: layout has '{f['label']}' at {key[0]}-{key[1]} that the PDF table lacks")
    return problems


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    problems, checked = [], 0
    ft = parse_tables(argv[1], re.compile(r"Record Identifier (FT\d\d) \(Input\)"), None)
    for rid, spec in catair.LAYOUT.items():
        fields = ft.get(rid)
        if not fields:
            problems.append(f"{rid}: no field table found in the PDF")
            continue
        # a table can repeat on a continuation page; de-duplicate identical rows
        uniq = list(dict.fromkeys(fields))
        problems += compare(rid, uniq, spec["fields"])
        checked += len(uniq)
    if len(argv) > 2:
        env = parse_tables(argv[2], re.compile(r"^(?:.*\s)?Input ([ABYZ])-Record\b"), None)
        for rid, fields in catair.ENVELOPE.items():
            pdf_fields = env.get(rid)
            if not pdf_fields:
                problems.append(f"{rid}-record: no field table found in the PDF")
                continue
            problems += compare(f"{rid}-record", list(dict.fromkeys(pdf_fields)), fields)
            checked += len(set(pdf_fields))
    print(f"fields compared against the PDF: {checked}")
    for p in problems:
        print("DIFFERENCE:", p)
    print("RESULT:", "layouts match the PDF" if not problems else f"{len(problems)} difference(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
