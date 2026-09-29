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

## Read this before relying on it

* It records and validates FTZ data; it is **not** an ACE-certified filer and its printouts are worksheets, not official CBP forms.
* Transit-day defaults for IT/TE/IE (30) are **placeholders** — set them to the limit your port grants (Setup → Settings).
* The rules encoded here (e.g. IE same-port, ZR restrictions) should be confirmed with your customs broker/FTZ operator.
* Locally it binds to `127.0.0.1` with no login. To host it, set `FTZ_AUTH_USER`/`FTZ_AUTH_PASSWORD` (the server refuses a public
  bind without them) and follow [DEPLOY.md](DEPLOY.md) (Render or a VPS with HTTPS).
