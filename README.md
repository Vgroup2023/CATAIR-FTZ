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

## Front page and client ERP connections
* **Front page** (what visitors see before signing in): the tagline *"Every unit accounted for. Every audit answered."*, what the system does, how it works, a "Connect the ERP you already run" section, and the sign-in / demo / forgot-password cards. Set `FTZ_CONTACT_EMAIL` to add a "Talk to us about your ERP" button that opens an email to that address.
* **The page's claims are limited to what is built.** It says connections use secure API keys and CSV files, lists common ERP names only to show what customers connect, and states that no endorsement, certification or vendor-built plug-in is implied. Please keep it that way if you edit the copy; add a connector claim only once the connector exists.
* **Client ERP connections** (*Integrations & Parts*): for each client, record the ERP system, how it connects (API / CSV / not yet), a link to their ERP or portal, and notes. Only `https://` links are accepted (no embedded passwords, no markup); they open in a new tab with `noopener`. The shared demo account can view but not change them. The **Connection guide & CSV templates** button lists the exact columns and endpoints for the parts, receiving-log and inventory-count feeds and downloads a ready-made CSV for each.

## CATAIR FT filing (e214 electronic admission)
*CATAIR FT Filing* builds the FT (Input) records for an admission as the ACE CATAIR Foreign Trade Zone chapter defines them (v3.1.3, Aug 2026, Pub # 0875-0826): 80-character fixed-width records FT10, FT11/12, FT20, FT40-43, FT50/51, FT60/61, laid out by the chapter's input record usage map (loops: conveyance 999, bill of lading 9,999, HTS line 9,999) and its per-action maps (Add, Replace, Status Change, Temporary Deposit, Delete).
* Start blank, or press **Build CATAIR FT filing** on an e214 to prefill header, bill and HTS lines from it. Anything the e214 does not hold (MID, mode of transport, dates) is listed for you to enter; nothing is invented.
* The side panel checks the filing live (field class/length, required fields, loop limits, usage map) and shows the exact 80-column records with a ruler.
* **Export** gives the complete batch (A, B, FT records, Y, Z) or the body only. Fill *Setup → Settings → ABI envelope* first. The ABI password is typed per export and never stored; each export is logged (without the password) and a filing that was exported cannot be deleted.
* `python3 tools/verify_catair.py FTZ_CATAIR.pdf [BatchBlock.pdf]` re-checks the code's layouts against the CBP PDF (121 fields).
* **Limits:** this does not transmit to CBP; use your ABI software or broker. A/B/Y/Z come from the separate ABI Batch and Block Control chapter (Oct 2021 draft) and FT is treated as ESAR-style there; confirm with your ABI vendor. Mode-of-transport codes are only checked as 2 digits. CBP-side checks (bond, FIRMS, HTS validity, Prior Notice) cannot be done offline.

## Install it on a computer, tablet or phone
Open your site (it must be served over **HTTPS**, which Render and the VPS guide both do) and install it like an app: its own icon, its own window, no browser bars.

| Device | How |
|---|---|
| **Windows / Mac / Chromebook** (Chrome or Edge) | Click the install icon at the right of the address bar, or the **Install** button on the sign-in page, or menu → *Install FTZ Control*. |
| **Android** (Chrome) | Tap **Install this app on your device** on the sign-in page, or menu ⋮ → *Install app / Add to Home screen*. |
| **iPhone / iPad** (Safari) | Tap **Share** → **Add to Home Screen** → *Add*. (iOS only offers this in Safari.) |
| **Mac** (Safari 17+) | File → *Add to Dock*. |

It is the same live system in every window, so changes show on all devices at once. **It does not work offline by design**: the ledger, FIFO checks and audit trail live on the server, and offline edits could not be reconciled safely. Offline you get a friendly "you're offline" page. The service worker stores only the look-and-feel files, never pages, records, documents or exports, so a shared device cannot show someone else's data. After you sign out, nothing private remains on it.

*Want it in the App Store or Google Play instead?* That needs the same site wrapped in a small native shell (and developer accounts); it is not included here.

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
