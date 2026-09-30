# CATAIR-FTZ — Standalone FTZ e214 / e216 & In-Bond System

A self-contained system for Foreign-Trade Zones and US Customs bonded warehouses. Python 3.9+ standard library only
(SQLite database, built-in web server, browser UI) — nothing to install.

```
python3 -m ftz            # http://127.0.0.1:8214   (FTZ_HOST, FTZ_PORT, FTZ_DB env vars)
python3 -m unittest discover -s tests -t .
```

## What it does

| Area | Behaviour |
|---|---|
| **e214** admission | Header + lines, per-line status **PF / NPF / D / ZR**. Layout splits into **214 → 214B** or, with Census statistics, **214A → 214C** (lines per sheet configurable). Draft → submitted → approved/rejected. Approval (with CBP reference) creates traceable inventory lots. PF requires a locked duty rate; foreign lines require HTSUS (NNNN.NN.NNNN) and country of origin. |
| **e216** permits | Blanket (≤12 months) or individual permits covering *manipulate, manufacture, exhibit, destroy, temporary removal*. Every activity is checked against an **active** permit that covers the activity, date and zone. Manufacture creates a finished-goods lot inheriting status; temporary removals track partial returns and overdue dates. |
| **In-bond (7512)** | **IT (61)**, **TE (62)**, **IE (63)** tabs. IT/TE must go to a different port, IE exports at the port of arrival; ZR goods cannot go IT. Issuing draws down zone stock (or manual lines for outside cargo); lifecycle issued → departed → arrived → closed (IT needs the entry number, TE/IE need proof of export); cancel restores stock; due-date and late-arrival flags. |
| **Inventory** | Lot ledger with full movement history. Withdraw to consumption (PF uses the locked rate, NPF needs the rate in force, ZR blocked), export, or duty-free zone-to-zone transfer; audited adjustments. |
| **Reports / audit** | Dashboard alerts, weekly-entry basis, inventory reconciliation, CSV export of every table, full DB backup, operator-attributed audit log, printable form worksheets. |

The feature set follows the areas listed for Thomson Reuters ONESOURCE FTZ Management (traceability, weekly entry,
duty-free scrap/re-export/transfers, reporting). Not yet built: bill-of-materials driven consumption, a PGA database,
and ACE/AES transmission.

## Ledger controls, automation and integrations

| Need | What the system does |
|---|---|
| **FIFO** | Withdrawals, in-bond issues and manufacturing inputs must use the oldest lot of the same item (zone + status + part) first, and are refused otherwise with the lot to use. **FIFO withdraw** does the splitting across lots for you. *Setup → Inventory method* can switch to specific identification, but only where CBP has approved it. Destruction and temporary removal are lot-specific by nature and are exempt. |
| **Dual units** | Each line carries a commercial quantity (PCS, boxes) and an optional customs quantity (KG…). Partial movements split the customs quantity proportionally, and the last piece takes the remainder, so the two units always close to exactly zero together. |
| **Hard stops** | Stock cannot go negative (enforced by the database itself), and nothing can be withdrawn, processed or moved in-bond dated before its lot was admitted. |
| **Audit trail** | The audit log and stock ledger are append-only (the database rejects edits/deletes), and every row is fingerprinted with a hash chain. *Audit & Export → Verify history integrity* recomputes it and shows the latest fingerprint to note outside the system. |
| **Smart mapping** | *Integrations & Parts* holds your product master (HTSUS, origin, units, conversion, duty rate, default status, PGA agencies). Entering a part number fills the e214 line; mismatches and PGA-flagged parts are flagged before submission. The master is yours to maintain: nothing here classifies goods on its own. |
| **WMS validation** | Import WMS receiving logs (CSV or API). *Run checks* / *Submit* compare each e214 with the receipts on the same B/L and warn about short, missing or extra parts; *Setup → WMS receiving mismatch* can make that block submission. |
| **Reconciliation** | Runs daily (and on demand): every lot's balance against its movement history, the latest WMS physical count against the customs ledger (overages/shortages), WMS receipts with no e214, e214-versus-receiving mismatches, and overdue in-bonds / removals. Results appear on the dashboard and in Reports. |
| **3461 / 7501 data** | Reports → Weekly entry links each entry number to a worksheet built from the ledger (lots, HTSUS, origin, quantities, value, duty, the e214 it came from). |

### Feeding it from an ERP or WMS
An administrator creates a token under *Integrations & Parts → API access* (shown once). Tokens can only push data; they cannot approve, withdraw or read anything else.

```bash
curl -X POST https://YOUR-SITE/api/wms/receipts -H "Authorization: Bearer ftz_..." -H "Content-Type: application/json" \
  -d '{"rows":[{"receipt_no":"R1001","part_no":"SPK-100","qty":500,"uom":"PCS","received_on":"2026-09-29","ref":"BL12345"}]}'
# also: POST /api/wms/inventory {"snapshot_on":"2026-09-29","rows":[{"part_no":"SPK-100","qty":497,"uom":"PCS"}]}
#       POST /api/parts/import {"rows":[{"part_no":"SPK-100","htsus":"8518.22.0000","coo":"CN","uom":"PCS"}]}
#       POST /api/admissions   (creates an e214 *draft* for a person to check and submit)
```

**Not built:** ready-made connectors for specific ERP/WMS products or EDI, automatic tariff classification, and transmission to ACE (you file through ACE or your broker; the worksheets carry the data).

## Read this before relying on it

* It records and validates FTZ data; it is **not** an ACE-certified filer and its printouts are worksheets, not official CBP forms.
* Transit-day defaults for IT/TE/IE (30) are **placeholders** — set them to the limit your port grants (Setup → Settings).
* The rules encoded here (e.g. IE same-port, ZR restrictions) should be confirmed with your customs broker/FTZ operator.
* There is a sign-in front page with self-service password reset and an optional public demo login. On first run with no accounts it
  prints a one-time `admin` password. Follow [DEPLOY.md](DEPLOY.md) to host it (Render or a VPS with HTTPS).
