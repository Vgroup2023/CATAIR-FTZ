"""Training and SOP content for new users: one source for the in-app Training tab and the downloadable OpenDocument (.odt) guide.

Block types: h3, p, ul, ol, check (checklist), note (kind tip|warn|info), table (head, rows, widths). Inline: **bold** and `code`.
Keep the wording in step with the screens: tests/test_training.py checks that every menu name appears here.
"""
import io
import re
import zipfile
from datetime import date
from xml.sax.saxutils import escape

TITLE = "Globlexus FTZ Control"
SUBTITLE = "Training and Standard Operating Procedures: how to use the application"
VERSION = "1.0"


def P(text): return {"t": "p", "text": text}
def UL(*items): return {"t": "ul", "items": list(items)}
def OL(*items): return {"t": "ol", "items": list(items)}
def CHECK(*items): return {"t": "check", "items": list(items)}
def H3(text): return {"t": "h3", "text": text}
def NOTE(kind, text): return {"t": "note", "kind": kind, "text": text}
def TABLE(head, rows, widths=None): return {"t": "table", "head": head, "rows": rows, "widths": widths}


SECTIONS = [
    {"id": "welcome", "title": "1. Welcome and how to use this guide", "audience": "Everyone", "minutes": 5, "blocks": [
        P("FTZ Control keeps the records of a foreign-trade zone or bonded warehouse in one place: what was admitted (e214), what activity was permitted (e216), "
          "what moved in-bond (7512), what stock is on hand and in what order it must be used, and who did what and when. It also prepares the data files your team or broker files in ACE."),
        P("This guide has two jobs. **Part A (sections 1 to 4)** explains the basics. **Part B (sections 5 to 14)** is a set of step-by-step procedures (SOPs) you can follow at your desk. "
          "Sections 15 to 19 are checklists, troubleshooting and reference."),
        TABLE(["If you are…", "Read these sections"], [
            ["Everyone, on day one", "1, 2, 3, 4"],
            ["Admissions clerk / data entry", "5, 6, 7, 11, 14"],
            ["Warehouse, shipping and receiving", "8, 9, 10, 11"],
            ["Compliance manager / permit holder", "5 to 16"],
            ["Administrator", "5, 13, 14 and all of Part B"],
            ["Customs auditor or adviser (read-only)", "2, 3, 4, 12, 17"]], [3, 3]),
        NOTE("info", "**Scope.** This guide explains how to operate the software. It is not legal advice. The rules you must follow come from U.S. Customs and Border Protection (CBP), "
                     "the FTZ Board, your zone grant and your customs counsel or broker. FTZ Control **prepares and records**; it does **not** transmit filings to CBP. Your team or broker files in ACE."),
        P("Work through the sections in order the first time. Afterwards, use the table of contents to jump to the procedure you need. At the end there is a training record to sign off.")]},

    {"id": "first-day", "title": "2. Your first day: sign in and secure your account", "audience": "Everyone", "minutes": 15, "blocks": [
        OL("Open the web address your administrator gave you. Enter your **user name** and **password**. If you were given a temporary password, change it right away (step 3).",
           "After you sign in you land on the **Dashboard**. The menu on the left (or across the top on a phone) lists every screen.",
           "Open **My Account** and choose **Change password**. Use at least 10 characters. Common passwords and passwords that contain your user name are refused.",
           "Still in **My Account**, choose **Set up two-factor**. In your authenticator app (Google Authenticator, Microsoft Authenticator, 1Password or Authy) add an account using the setup key shown, then type the 6-digit code and press **Turn on**.",
           "The app then shows **8 recovery codes**. Copy them to a safe place (a password manager or a locked drawer). **They are shown only once** and each works one time if you lose your phone.",
           "Optional: choose **Set email**. If your administrator has set up email, you can then reset your own password from the sign-in page.",
           "Optional: install the app on your computer, tablet or phone. Use your browser's **Install app** or **Add to Home Screen** option."),
        NOTE("warn", "Five wrong passwords or codes in a row block sign-in for 15 minutes. Sessions end after 12 hours. On a shared computer always press **Sign out**."),
        H3("Your role decides what you can do"),
        TABLE(["Role", "What it can do"], [
            ["Administrator", "Everything, plus the Users screen, API tokens and the full backup."],
            ["User", "Day-to-day work: admissions, permits, activity, inventory, in-bond, documents, CATAIR files, reports."],
            ["Auditor (read-only)", "View and export everything. Any attempt to change something shows: \"this is a read-only auditor account\"."]], [2, 5]),
        P("If you cannot see a menu item or a button, your role is the usual reason. Ask your administrator.")]},

    {"id": "tour", "title": "3. Finding your way around", "audience": "Everyone", "minutes": 10, "blocks": [
        TABLE(["Menu item", "What it is for"], [
            ["Dashboard", "Counts of open work and a **Needs attention** list. Start every day here."],
            ["e214 Admissions", "Applications to admit goods to the zone, and their status."],
            ["CATAIR FT Filing", "Builds the electronic e214 (FT) and FZ files, and reads CBP responses."],
            ["Documents", "Every supporting file (invoices, bills of lading, notices) in one searchable list."],
            ["e216 Permits & Activity", "Permits for manipulation, manufacture, exhibition, destruction and temporary removal, and the activity log."],
            ["Zone Inventory", "Stock by lot, FIFO withdrawals, history and adjustments."],
            ["In-Bond IT · TE · IE", "In-bond movements (CBP Form 7512)."],
            ["Reports", "Daily reconciliation, weekly entry basis, inventory and activity summaries."],
            ["Integrations & Parts", "Product master, WMS feeds, client ERP connections and API tokens."],
            ["Setup", "Zones, parties and system settings."],
            ["Audit & Export", "The audit log, history integrity check, CSV exports and the full backup."],
            ["Training & SOP", "This guide, and the downloadable copy."],
            ["My Account", "Your password, two-factor and email."],
            ["Users", "Administrators only: accounts, roles, password and two-factor resets."]], [2, 5]),
        H3("The Dashboard"),
        UL("The tiles show how many e214 are waiting for CBP, how many are drafts, how many e216 permits are active, how many IT, TE and IE movements are open, and the value of stock by status (PF, NPF, D, ZR).",
           "**Needs attention** lists overdue in-bonds, in-bonds that arrived but are not closed, temporary removals that are overdue, permits about to expire, e214 waiting for a CBP answer, and reconciliation problems.",
           "Click a tile or an alert to go straight to that screen."),
        H3("How the screens behave"),
        UL("Click a row to open its details. Forms open in a window; **Save draft** keeps your work without making anything official.",
           "Coloured tags show status: green is good or complete, amber needs a look, red needs action.",
           "Everything you save is recorded with your name and the time in the audit log. You cannot delete history."),
        NOTE("tip", "Press the menu item again to refresh a screen. After an update, a refresh loads the newest version.")]},

    {"id": "concepts", "title": "4. Key terms you will meet", "audience": "Everyone", "minutes": 10, "blocks": [
        TABLE(["Term", "Meaning in this application"], [
            ["FTZ", "Foreign-Trade Zone: a secure area treated as outside U.S. customs territory for duty purposes until goods are entered for consumption."],
            ["e214", "Application for FTZ admission and status designation. In FTZ Control it creates the **lots** that make up your inventory once CBP approves it."],
            ["e216", "Application for permission to do an activity in the zone: manipulate, manufacture, exhibit, destroy or temporarily remove goods."],
            ["CBP / ACE reference", "The approval or case number CBP gives. You type it in when you record an approval."],
            ["PF  Privileged Foreign", "Foreign goods whose duty rate is **locked** at admission. The rate is fixed from the e214."],
            ["NPF  Non-Privileged Foreign", "Foreign goods whose duty rate is the one **in force when they leave** for U.S. consumption. You enter it on the withdrawal."],
            ["D  Domestic", "U.S. goods in the zone."],
            ["ZR  Zone-Restricted", "Goods that may only be exported, destroyed or moved by transportation and exportation (TE) or immediate exportation (IE)."],
            ["Lot", "A quantity admitted on one e214 line. Every lot traces back to its e214, and each lot has a receipt date."],
            ["FIFO", "First in, first out. Older stock must be used before newer stock of the same item. This is the default setting."],
            ["Customs quantity", "The second unit CBP tracks (for example KG or DOZ), shown next to your own unit (for example PCS)."],
            ["HTSUS", "Harmonized tariff number, written 0000.00.0000."],
            ["COO / MID", "Country of origin, and Manufacturer Identification code (needed on each line of an electronic e214 filing)."],
            ["In-bond (7512)", "Movement of goods under bond. **IT** (61) goes to another port for consumption entry, **TE** (62) goes to another port for export, **IE** (63) is exported at the port of arrival."],
            ["FZ message", "A follow-up to an admission: operator acceptance or refusal, concurrence with received quantities, permit to transfer (PTT), arrival, or post-admission correction."],
            ["Reconciliation", "A check that the stock ledger, the lot balances and your warehouse system (WMS) agree."],
            ["Void", "How a wrong document is retired. It stays on record; nothing is deleted."]], [2, 5])]},

    {"id": "setup", "title": "5. One-time set-up before the first admission", "audience": "Administrator / compliance", "minutes": 30, "blocks": [
        P("Do this once. Until it is done, you cannot create an e214."),
        OL("Open **Setup** and press **+ Zone**. Enter the zone number (for example FTZ 123), name, grantee, port code and, for electronic filing, the 4-character **FIRMS code**.",
           "Press **+ Party** for each company you deal with: zone operator, importer, consignee, carrier, broker and surety. Put the Importer of Record number or the carrier's SCAC code in the ID field.",
           "Scroll to **Settings** and review each one (see the table below).",
           "Open **Integrations & Parts** and add your products to the **product master** (section 13). This saves typing on every e214 line."),
        TABLE(["Setting", "What to set"], [
            ["Inventory method", "Leave on **FIFO**. Choose specific identification only if CBP has approved it for you."],
            ["WMS receiving mismatch", "**Warn only** (default) or **Block e214 submission**. Blocking is stricter."],
            ["Lines per 214 / 214A sheet", "How many lines print on each sheet (default 10)."],
            ["IT / TE / IE transit days", "The defaults are placeholders. Change them to the time limit CBP gives you at your port for each type."],
            ["Permit expiry warning (days)", "How early the Dashboard warns that an e216 is about to expire."],
            ["ABI envelope codes", "Site code, sender ID, office, filer and port codes. Needed only to export complete CATAIR files (section 7)."]], [2, 5]),
        NOTE("warn", "The transit-day defaults are not CBP limits. Wrong values make the overdue alerts wrong.")]},

    {"id": "sop-admit", "title": "6. SOP 1: Admit merchandise to the zone (e214)", "audience": "Admissions clerk", "minutes": 25, "blocks": [
        P("**Purpose:** record an application to admit goods, check it, and turn it into stock once CBP approves. **Who:** admissions clerk. **When:** each time goods arrive for the zone."),
        TABLE(["Status", "Meaning", "What you can do"], [
            ["draft", "Saved, not yet sent", "Edit, run checks, submit, delete"],
            ["submitted", "Sent to CBP, waiting for an answer", "Record approval or rejection"],
            ["approved", "CBP approved: lots now exist in Zone Inventory", "Look up, print, attach documents"],
            ["rejected", "CBP refused", "Edit, correct, submit again"]], [1.5, 3, 3]),
        OL("Open **e214 Admissions** and press **+ New e214**.",
           "Fill the header: **Zone**, **Zone operator**, **Importer of record**, **Carrier** (required), **Mode**, **Bill of lading / AWB** (required), vessel or flight, **Port of entry** code (required), **Admission date** (required) and, if the goods arrived in-bond, the 7512 number. Tick **Report Census statistics** if it is a 214A/214C case.",
           "Add a line for each product. Type the **Part number**: description, HTSUS, origin, unit, status and duty rate fill in from the product master. Then enter **Qty**, **Value USD**, the **Status** (PF, NPF, D or ZR), the **Customs qty** (second unit) and the **Location**. If the part is flagged for another agency (FDA, USDA and so on), enter the **PGA ref.**",
           "Press **Save draft**. Open the new record from the list.",
           "Press **Run checks**. **Blocking** items must be fixed. **Warnings** (a part not in the master, a tariff number or origin that differs from the master, a customs quantity that does not match the conversion, an agency review flag, or goods that do not match your WMS receiving log) need your judgement.",
           "Press **Submit to CBP** when the checks are clean. This marks the e214 as submitted in FTZ Control. File it in ACE through your broker, or prepare the electronic file in section 7.",
           "When CBP answers, open the e214 again. For an approval press **Record CBP approval** and enter the CBP or ACE reference: the goods become lots in **Zone Inventory**. For a refusal press **Record rejection** with the reason, then **Edit** and correct it.",
           "Attach the invoice, packing list and bill of lading in the **Documents** panel at the bottom of the record (section 11). **Print worksheet** gives a paper copy."),
        NOTE("tip", "Enter the part number first. If the part is in the product master, most of the line is filled for you and typing mistakes disappear."),
        NOTE("warn", "Approve only when CBP has actually approved. The system will not let you approve without a reference, and every approval is logged with your name.")]},

    {"id": "sop-catair", "title": "7. SOP 2: Prepare the CATAIR file and read CBP's answer", "audience": "Admissions clerk / compliance", "minutes": 30, "blocks": [
        P("**Purpose:** build the electronic e214 (FT) records and the follow-up (FZ) records exactly as the ACE CATAIR Foreign Trade Zone chapter defines them, and read the NF response CBP sends back. "
          "**Remember:** FTZ Control prepares the file. Your ABI software or broker sends it."),
        H3("Build an FT (e214) file"),
        OL("Open the e214 and press **Build CATAIR FT filing**. The **CATAIR FT Filing** screen opens with the header, bill and lines filled from the e214.",
           "Read the **Needs your input** list on the right. It names what the e214 does not hold, for example the full Zone ID, the FIRMS code, the Importer of Record number, the mode of transportation, dates, port of unlading, the bill quantity, weight, transportation charges and the MID for each line. Type each one in.",
           "Watch the **Checks** tab. It shows blocking problems and warnings as you type. Fix until it says there are no blocking problems.",
           "Open the **Records** tab to see the exact 80-character records, with a ruler. **Copy records** copies them.",
           "Press **Save**, then **Export…**. Choose the **complete batch** (A, B, FT records, Y, Z) or **body only**, choose the line ending (ask your ABI software if unsure), and type the **ABI communication password**. The password is used for this download only and is never stored.",
           "Give the file to your ABI software or broker. Each export is logged with a fingerprint, and a filing that was exported cannot be deleted."),
        NOTE("info", "For the complete batch, the ABI envelope codes must be filled in under **Setup**, **Settings**."),
        H3("Other FT actions"),
        UL("**Replace (R):** retransmits the whole admission with changes. Choose every reason that applies. Reason 08 (Other) needs a remark.",
           "**Delete (D):** sends only the header and optional comments.",
           "**Status change (S):** changes the zone status of goods already admitted."),
        H3("FZ messages (follow-ups)"),
        P("Press **+ FZ action** on the CATAIR FT Filing screen. Choose the action:"),
        TABLE(["Action", "Use it to…", "You will need"], [
            ["H / G", "Operator **accepts** or **refuses** the goods", "The admission number"],
            ["A, B, C, D", "**Concur** with the delivered quantity (all, one bill, one in-bond, one container)", "Received quantity and delivery code FI (final) or PA (partial)"],
            ["F", "Request a **permit to transfer** (PTT)", "Quantity, bonded carrier IRS number, destination FIRMS code (airport code for air)"],
            ["K", "**Cancel** a PTT", "The PID CBP returned when the PTT was accepted"],
            ["I", "Report **physical arrival** at the zone", "Destination FIRMS code"],
            ["J", "Request a **post-admission correction**", "Contact, reason 01 to 11, and the replacement number for reasons 02 to 05"]], [1.3, 3.5, 3.2]),
        H3("Read CBP's response"),
        OL("When CBP's NF response arrives from your ABI software or broker, open **CATAIR FT Filing** and press **Paste CBP response…**.",
           "Paste the text. **Read only** decodes it. **Apply to filings** also records it and moves the matching filing to **accepted**, **rejected** or **authorized**.",
           "For a rejection, the response shows the record CBP found in error. Fix the filing, export again and resubmit.")]},

    {"id": "sop-permits", "title": "8. SOP 3: e216 permits and zone activity", "audience": "Compliance / warehouse", "minutes": 20, "blocks": [
        P("**Purpose:** make sure every manipulation, manufacture, exhibition, destruction or temporary removal happens under an active permit and is recorded."),
        H3("Create and activate a permit"),
        OL("Open **e216 Permits & Activity** and press **+ New e216 permit**.",
           "Choose the **Zone**, **Operator**, **Permit type** (Blanket (Annual) or Individual / Specific), **Valid from** and **Valid to**, tick the **Activities requested**, and describe the merchandise, activity and location. A blanket permit covers at most 12 months.",
           "Press **Save draft**. Open the permit and, when CBP approves it, press **Activate permit** and enter the CBP approval reference.",
           "Press **Revoke** if a permit must be stopped. Attach the approval letter in the **Documents** panel."),
        H3("Record an activity"),
        OL("Press **+ Record activity**.",
           "Choose the **e216 permit** (only active ones are listed), the **Activity**, the **Lot**, the **Quantity**, the **Date** and the new location.",
           "For **manufacture**, also enter the finished quantity, unit, description and HTSUS. For **destroy**, note the method and disposition. For **temporary removal**, enter the **Expected return** date.",
           "Press **Record**. The permit must be active and cover both the date and the activity, or the system refuses.",
           "When goods come back from a temporary removal, open the **Activity log** tab and press **Record return**."),
        NOTE("warn", "Zone-Restricted (ZR) goods can only be exported, destroyed or moved TE/IE. The system will tell you if you try anything else."),
        NOTE("tip", "The Dashboard warns when a permit is near expiry and when a temporary removal is overdue. Do not wait for CBP to notice first.")]},

    {"id": "sop-inventory", "title": "9. SOP 4: Inventory, withdrawals and adjustments", "audience": "Warehouse / shipping", "minutes": 20, "blocks": [
        P("**Purpose:** take stock out of the zone correctly, in the right order, with the right paperwork, so the ledger always matches the shelf."),
        H3("Look at stock"),
        UL("Open **Zone Inventory**. Each row is a lot with its status, receipt date, quantity on hand (in both units), quantity temporarily out and value.",
           "Use **Status filter** to see only PF, NPF, D or ZR. **Export CSV** saves the list.",
           "Press **History** on a lot to see every movement: admission, withdrawals, transfers, activities and adjustments."),
        H3("Withdraw stock (normal case: FIFO)"),
        OL("Press **FIFO withdraw**.",
           "Choose the **Item**. The oldest lot is used first.",
           "Choose **Withdraw to**: U.S. consumption (7501 entry), Export, or Duty-free transfer to another zone.",
           "Enter the **Total quantity** and the **Date**. For consumption add the **entry number**. For NPF goods enter the **duty rate in force**. For export or transfer enter the reference (for example the AES ITN).",
           "Press **Withdraw oldest first**. A message lists the lots used and the estimated duty."),
        H3("Withdraw from one lot"),
        P("Press **Withdraw** on a lot. If an older lot of the same item still has stock, the system stops you and names it. Use FIFO withdraw instead, or change the method in **Setup** if CBP has approved it."),
        H3("Correct a count"),
        OL("Press **History** on the lot, then **Adjust on-hand**.",
           "Enter the change (plus or minus) and a **reason**. A reason is required.",
           "The adjustment is logged with your name. The system never lets stock go below zero."),
        H3("File the weekly consumption entry"),
        P("Open **Reports**. **Weekly entry filing** lists consumption withdrawals by week and status. Click an entry number to open and print its CBP Form 3461 / 7501 data worksheet.")]},

    {"id": "sop-inbond", "title": "10. SOP 5: In-bond movements (7512)", "audience": "Warehouse / compliance", "minutes": 20, "blocks": [
        P("**Purpose:** move goods under bond to another port (IT or TE) or export them at the port of arrival (IE), and close the movement with proof."),
        OL("Open **In-Bond IT · TE · IE** and press **+ Issue in-bond**.",
           "Choose the **In-bond type**, the **Origin zone**, the **Bonded carrier**, consignee and surety, the bond number, **Mode**, **B/L or AWB**, **Origin port** and **Destination / export port** codes, and the **Issue date**. Enter the CBP-assigned in-bond number if you have it.",
           "Under **Merchandise**, pick a zone lot to draw the stock down, or enter a manual line for cargo that did not come from the zone.",
           "Press **Issue in-bond**. A due date is set from the transit days in **Setup**.",
           "Open the movement and press **Depart** when it leaves, then **Arrive** when it reaches the destination.",
           "Press **Close**. For **IT** enter the consumption entry number. For **TE** and **IE** enter the exporting carrier and the foreign destination.",
           "If it never left, press **Cancel**. The stock returns to inventory."),
        NOTE("warn", "Overdue and late-arriving movements are flagged red on the Dashboard. A late arrival can mean liquidated-damages exposure with CBP."),
        NOTE("tip", "Attach the 7512 copy and proof of export in the **Documents** panel of the movement.")]},

    {"id": "sop-documents", "title": "11. SOP 6: Keep supporting documents with the record", "audience": "Everyone who handles paper", "minutes": 10, "blocks": [
        P("**Purpose:** store every invoice, bill of lading, notice and permit letter against the record it supports, so an auditor can see it in seconds."),
        OL("Open the e214, e216 or in-bond record. At the bottom is the **Documents** panel. Press **+ Attach**.",
           "Choose the file, choose its **Type** (invoice, packing list, bill of lading, CBP notice, permit, bond, certificate, correspondence or other) and add a short note.",
           "Press **Upload**. The file is fingerprinted when it is stored. Sending the same file twice just shows it is already attached.",
           "Open the **Documents** menu to search every document by name, note or type. Use **+ General document** for items that belong to the whole operation, such as a bond rider.",
           "Click a file name to download it. Every download is logged."),
        TABLE(["Accepted", "Not accepted"], [["PDF, PNG, JPG, CSV, TXT, DOCX, XLSX, DOC, XLS up to 8 MB", "Programs, scripts, web pages, files whose content does not match their type"]], [4, 4]),
        NOTE("info", "**Nothing is deleted.** If you attached the wrong file, press **Void** and give a reason. The file stays on record and is marked voided. Upload the correct one beside it."),
        NOTE("tip", "Name files so they sort and read well, for example `e214-2026-0003 commercial invoice 4471.pdf`."),
        P("If a download is refused because \"this stored document no longer matches its fingerprint\", stop and tell your administrator. It means the stored file was altered.")]},

    {"id": "sop-reports", "title": "12. SOP 7: Reconciliation, reports and the audit trail", "audience": "Compliance / auditor", "minutes": 20, "blocks": [
        P("**Purpose:** prove, at any time, that the records are complete, consistent and untouched."),
        H3("Reconciliation"),
        OL("The system reconciles once a day automatically. To run it now, open **Reports** and press **Run reconciliation now**.",
           "Read the result. It compares lot balances with the movement ledger and your WMS physical count with the customs ledger, and reports receipts that never became an e214.",
           "For every issue, find the cause (a missed withdrawal, a wrong count, a missing receipt) and fix it with the proper record. Do not adjust a balance without a reason."),
        H3("Reports"),
        UL("**Weekly entry filing:** consumption withdrawals by week, status and entry number.",
           "**Inventory by zone and status:** admitted, on hand, temporarily out and value.",
           "**Activity summary** (e216) and **In-bond summary**.",
           "**Recent reconciliation runs:** when, by whom, how many issues."),
        H3("Audit & Export"),
        OL("Open **Audit & Export**. The log shows when, who, what and which record.",
           "Press **Verify history integrity**. It recomputes the fingerprints of the audit log and the stock ledger, and re-checks every stored document. It should say verified.",
           "For an audit request, download the CSV you need (admissions, lines, lots, movements, permits, activities, in-bonds, audit).",
           "Administrators can also download the **Full backup**."),
        NOTE("tip", "Write down the fingerprint shown by the integrity check each week and store it outside the system. It proves that history was not rewritten later.")]},

    {"id": "sop-integrations", "title": "13. SOP 8: Product master and connecting your ERP or WMS", "audience": "Administrator / data owner", "minutes": 25, "blocks": [
        H3("Product master"),
        OL("Open **Integrations & Parts** and press **+ Part**.",
           "Enter the part number, description, HTSUS, country of origin, your unit, the customs unit, the conversion (customs units per your unit), the duty rate, the default status, any agencies to review and the **MID**.",
           "To load many at once press **Import CSV** with the columns `part_no, description, htsus, coo, uom, uom2, conv, duty_rate, default_status, pga_agencies, mid`."),
        H3("Warehouse system feeds"),
        UL("**WMS receiving log** (CSV: `receipt_no, part_no, qty, uom, received_on, ref`). The **ref** is the bill of lading or AWB. Each e214 is checked against it before submission.",
           "**WMS inventory count** (CSV: `part_no, qty, uom`, with the count date). The latest count is compared with the customs ledger in the daily reconciliation."),
        H3("Client ERP connections and API tokens"),
        UL("**+ Client connection** records where each client's master ERP lives and how it feeds this system. Only secure https links are accepted. **Connection guide & CSV templates** lists exact columns.",
           "Administrators press **+ New token** to let a system push parts, receipts and counts and create e214 drafts. The token is **shown once**: copy it into the other system. Tokens cannot approve anything, withdraw stock or read other data. **Revoke** a token the day the person or system no longer needs it.")]},

    {"id": "sop-accounts", "title": "14. SOP 9: Accounts, roles and security", "audience": "Administrator", "minutes": 15, "blocks": [
        OL("Open **Users** and press **+ New user**. Choose the **Role** (User, Auditor (read-only) or Administrator) and a temporary password of at least 10 characters. Tell the person to change it and set up two-factor on first sign-in.",
           "**Set password** replaces a forgotten password. The person is signed out everywhere.",
           "**Disable** stops a person signing in without deleting their history. **Enable** restores access.",
           "**Reset 2FA** removes two-factor for someone who lost their phone, and signs them out. They then set it up again.",
           "Keep at least one active administrator. The system will not let you disable the last one."),
        TABLE(["Good habit", "Why"], [
            ["One account per person, never shared", "The audit log must show who did what."],
            ["Two-factor on for every administrator", "A stolen password alone is then not enough."],
            ["Auditor role for outsiders", "They can see everything and change nothing."],
            ["Review the Users list monthly; disable leavers the same day", "Old accounts are the usual way in for the wrong person."],
            ["Revoke unused API tokens", "A token is a key to your data feeds."],
            ["Never type your password or code into a message or email", "No one from IT or support will ask for it."]], [3, 4])]},

    {"id": "checklists", "title": "15. Daily, weekly, monthly and yearly routines", "audience": "Everyone", "minutes": 5, "blocks": [
        H3("Every day"),
        CHECK("Open the Dashboard and clear **Needs attention** items.",
              "Record CBP answers for every submitted e214 (approval or rejection).",
              "Enter the day's receipts in your warehouse system so the WMS log is current.",
              "Record the day's withdrawals, transfers and activity the same day.",
              "Depart, arrive and close in-bond movements as they happen; record returns of temporary removals.",
              "Attach new paperwork to its record."),
        H3("Every week"),
        CHECK("Run the reconciliation and resolve every issue.",
              "File or prepare the weekly consumption entries from Reports.",
              "Review open in-bonds and overdue items.",
              "Run **Verify history integrity** and note the result."),
        H3("Every month"),
        CHECK("Import the WMS physical inventory count.",
              "Review the Users list and API tokens; disable leavers.",
              "Download the audit and ledger CSV exports and store them in your archive.",
              "Check permits that expire in the next 60 days."),
        H3("Every year"),
        CHECK("Renew blanket e216 permits (they cover at most 12 months).",
              "Re-check transit-day settings and ABI codes against your current CBP arrangements.",
              "Test restoring from a backup with your administrator.",
              "Repeat this training for new staff and as a refresher.")]},

    {"id": "troubleshooting", "title": "16. Troubleshooting and questions", "audience": "Everyone", "minutes": 5, "blocks": [
        TABLE(["What you see", "Why", "What to do"], [
            ["\"too many sign-in attempts\"", "Too many wrong passwords or codes", "Wait 15 minutes, then sign in carefully."],
            ["Lost your phone for two-factor", "No authenticator code", "Use one of your 8 recovery codes, or ask an administrator to **Reset 2FA**."],
            ["\"this is a read-only auditor account\"", "You are signed in as an Auditor", "Expected. Ask an administrator if you need to change records."],
            ["Cannot submit an e214", "Blocking checks, or the WMS block setting", "Press **Run checks** and fix the blocking items."],
            ["\"FIFO: lot … must be used before lot …\"", "An older lot still has stock", "Use **FIFO withdraw**, or correct the older lot first."],
            ["\"negative inventory blocked\"", "The quantity is more than is on hand", "Check the lot history and the quantity."],
            ["Export of the CATAIR file is refused", "Missing ABI envelope codes or password, or a blocking check", "Fill **Setup**, **Settings**, fix the checks, and type the 6-character password."],
            ["A document is refused", "Wrong type, too large, or the content does not match", "Use PDF, image, CSV, TXT, Word or Excel up to 8 MB."],
            ["Signed out suddenly", "Your 12-hour session ended", "Sign in again. Unsaved forms are lost, so save drafts often."],
            ["Reconciliation shows issues", "A record is missing or a count is wrong", "Follow section 12."],
            ["The screen looks out of date", "Your browser has an old copy", "Refresh the page."]], [2.5, 2.2, 3.3])]},

    {"id": "records", "title": "17. Record-keeping rules", "audience": "Everyone", "minutes": 5, "blocks": [
        UL("**Record it the same day.** The ledger is only as good as its timing.",
           "**Nothing is deleted.** Mistakes are corrected with an adjustment (with a reason), a void (with a reason), or a replacement record. The audit log keeps the story.",
           "**One source of truth.** Do not keep a second set of numbers in a spreadsheet. Export from here.",
           "**Keep the paper with the record.** Attach documents to the e214, e216 or in-bond they support.",
           "**Archive on a schedule.** Download the CSV exports and the backup and store them where your retention policy says.",
           "**Know your retention period.** Customs records are generally kept for several years. Confirm the period that applies to you with your broker or counsel.")]},

    {"id": "reference", "title": "18. Reference and local contacts", "audience": "Everyone", "minutes": 5, "blocks": [
        TABLE(["Item", "Values"], [
            ["e214 statuses", "draft, submitted, approved, rejected"],
            ["e216 permit statuses", "draft, active, revoked, expired"],
            ["In-bond statuses", "issued, in transit, arrived, closed, cancelled"],
            ["Zone statuses", "PF Privileged Foreign, NPF Non-Privileged Foreign, D Domestic, ZR Zone-Restricted"],
            ["In-bond types", "IT 61 Immediate Transportation, TE 62 Transportation & Exportation, IE 63 Immediate Exportation"],
            ["Withdraw to", "U.S. consumption (7501 entry), Export, Duty-free transfer to another zone"],
            ["Tariff number format", "0000.00.0000"],
            ["Document types", "invoice, packing list, bill of lading, CBP notice, permit, bond, certificate, correspondence, other"],
            ["FT actions", "A Add, D Delete, R Replace, S Status change"],
            ["FZ actions", "A to D concur, F PTT request, G refuse, H accept, I arrival, J correction request, K cancel PTT"]], [2, 5]),
        H3("Your local contacts (fill in)"),
        TABLE(["Role", "Name", "Phone / email"], [["Zone operator contact", "", ""], ["Application administrator", "", ""], ["Customs broker", "", ""], ["CBP port contact", "", ""], ["Customs counsel", "", ""], ["IT / hosting contact", "", ""]], [3, 2.5, 2.5])]},

    {"id": "sign-off", "title": "19. Training record", "audience": "Trainee and trainer", "minutes": 2, "blocks": [
        P("Tick each module the trainee has completed with the trainer, then sign."),
        CHECK("Signed in, changed the password and turned on two-factor (section 2)",
              "Toured the menu and the Dashboard (sections 3 and 4)",
              "Created and checked a practice e214 (section 6)",
              "Prepared a practice CATAIR file and read a response (section 7)",
              "Recorded a practice permit and activity (section 8)",
              "Made a practice FIFO withdrawal and an adjustment (section 9)",
              "Issued and closed a practice in-bond (section 10)",
              "Attached and voided a practice document (section 11)",
              "Ran a reconciliation and the integrity check (section 12)",
              "Understood the daily and weekly routines (section 15)"),
        TABLE(["", "Trainee", "Trainer"], [["Name", "", ""], ["Role", "", ""], ["Date", "", ""], ["Signature", "", ""]], [2, 3, 3])]},
]


# --------------------------------------------------------------------------------------------- JSON for the Training tab
def guide(today=None):
    return {"title": TITLE, "subtitle": SUBTITLE, "version": VERSION, "date": (today or date.today()).isoformat(), "sections": SECTIONS}


# --------------------------------------------------------------------------------------------- OpenDocument text (.odt)
NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
      'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
      'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
      'xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0" '
      'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"')
NAVY, GOLD, CREAM = "#0d2250", "#d0a339", "#f7efd9"
PAGE_W_IN = 6.7                                          # US Letter, 0.9 in margins


def inline(text):
    """**bold** and `code` to ODF spans; everything else escaped."""
    out = []
    for part in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text):
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            out.append(f'<text:span text:style-name="B">{escape(part[2:-2])}</text:span>')
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            out.append(f'<text:span text:style-name="Code">{escape(part[1:-1])}</text:span>')
        else:
            out.append(escape(part))
    return "".join(out)


def plain(text):
    return re.sub(r"\*\*|`", "", text)


class _Doc:
    def __init__(self):
        self.body, self.auto, self.n = [], [], 0

    def table(self, head, rows, widths):
        self.n += 1
        t = f"T{self.n}"
        widths = widths or [1] * len(head)
        total = float(sum(widths))
        self.auto.append(f'<style:style style:name="{t}" style:family="table"><style:table-properties style:width="{PAGE_W_IN}in" table:align="left" fo:margin-top="0.06in" fo:margin-bottom="0.1in"/></style:style>')
        cols = []
        for i, w in enumerate(widths):
            self.auto.append(f'<style:style style:name="{t}.C{i}" style:family="table-column"><style:table-column-properties style:column-width="{PAGE_W_IN * w / total:.3f}in"/></style:style>')
            cols.append(f'<table:table-column table:style-name="{t}.C{i}"/>')
        h = "".join(f'<table:table-cell table:style-name="TH" office:value-type="string"><text:p text:style-name="THP">{inline(c)}</text:p></table:table-cell>' for c in head)
        r = "".join("<table:table-row>" + "".join(
            f'<table:table-cell table:style-name="TD" office:value-type="string"><text:p text:style-name="TDP">{inline(c) if c else ""}</text:p></table:table-cell>' for c in row)
            + "</table:table-row>" for row in rows)
        self.body.append(f'<table:table table:name="{t}" table:style-name="{t}">{"".join(cols)}<table:table-header-rows><table:table-row>{h}</table:table-row></table:table-header-rows>{r}</table:table>')

    def block(self, b):
        k = b["t"]
        if k == "p":
            self.body.append(f'<text:p text:style-name="Body">{inline(b["text"])}</text:p>')
        elif k == "h3":
            self.body.append(f'<text:h text:style-name="H3" text:outline-level="3">{inline(b["text"])}</text:h>')
        elif k in ("ul", "ol"):
            style = "ListBullet" if k == "ul" else "ListNumber"
            items = "".join(f'<text:list-item><text:p text:style-name="Li">{inline(i)}</text:p></text:list-item>' for i in b["items"])
            self.body.append(f'<text:list text:style-name="{style}">{items}</text:list>')
        elif k == "check":
            for i in b["items"]:
                self.body.append(f'<text:p text:style-name="Check">☐  {inline(i)}</text:p>')
        elif k == "note":
            label = {"tip": "Tip", "warn": "Important", "info": "Note"}[b["kind"]]
            style = {"tip": "NoteTip", "warn": "NoteWarn", "info": "NoteInfo"}[b["kind"]]
            self.body.append(f'<text:p text:style-name="{style}"><text:span text:style-name="B">{label}: </text:span>{inline(b["text"])}</text:p>')
        elif k == "table":
            self.table(b["head"], b["rows"], b.get("widths"))
        else:
            raise ValueError(k)


def build_odt(logo_png=None, today=None):
    """Return the guide as OpenDocument text (.odt) bytes. Standard library only."""
    g = guide(today)
    d = _Doc()
    logo = ""
    if logo_png:
        logo = ('<text:p text:style-name="Center"><draw:frame draw:name="Logo" text:anchor-type="as-char" svg:width="1.7in" svg:height="1.7in">'
                '<draw:image xlink:href="Pictures/logo.png" xlink:type="simple" xlink:show="embed" xlink:actuate="onLoad"/></draw:frame></text:p>')
    d.body.append(logo)
    d.body.append(f'<text:p text:style-name="Title">{escape(g["title"])}</text:p>')
    d.body.append(f'<text:p text:style-name="Subtitle">{escape(g["subtitle"])}</text:p>')
    d.body.append(f'<text:p text:style-name="Center">Version {g["version"]}  ·  {g["date"]}</text:p>')
    d.body.append('<text:p text:style-name="Center">Prepared for new users of the Globlexus FTZ Control application</text:p>')
    d.body.append('<text:p text:style-name="Body"/>')
    d.body.append('<text:h text:style-name="ContentsH" text:outline-level="1">Contents</text:h>')
    for s in g["sections"]:
        d.body.append(f'<text:p text:style-name="Toc">{escape(s["title"])}  <text:span text:style-name="Muted">({s["audience"]}, about {s["minutes"]} min)</text:span></text:p>')
    first = True
    for s in g["sections"]:
        d.body.append(f'<text:h text:style-name="{"H1Break" if first else "H1"}" text:outline-level="1">{escape(s["title"])}</text:h>')
        d.body.append(f'<text:p text:style-name="Meta">For: {escape(s["audience"])}  ·  Time: about {s["minutes"]} minutes</text:p>')
        first = False
        for b in s["blocks"]:
            d.block(b)
    cell_styles = (f'<style:style style:name="TH" style:family="table-cell"><style:table-cell-properties fo:background-color="{NAVY}" fo:padding="0.05in" fo:border="0.5pt solid #999999"/></style:style>'
                   f'<style:style style:name="TD" style:family="table-cell"><style:table-cell-properties fo:padding="0.05in" fo:border="0.5pt solid #bbbbbb"/></style:style>')
    content = (f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {NS} office:version="1.2"><office:automatic-styles>{cell_styles}{"".join(d.auto)}'
               f'</office:automatic-styles><office:body><office:text>{"".join(d.body)}</office:text></office:body></office:document-content>')
    styles = f"""<?xml version="1.0" encoding="UTF-8"?><office:document-styles {NS} office:version="1.2">
<office:font-face-decls><style:font-face style:name="Liberation Sans" svg:font-family="'Liberation Sans', Arial, sans-serif"/><style:font-face style:name="Liberation Mono" svg:font-family="'Liberation Mono', 'Courier New', monospace"/></office:font-face-decls>
<office:styles>
<style:default-style style:family="paragraph"><style:paragraph-properties fo:orphans="2" fo:widows="2"/><style:text-properties style:font-name="Liberation Sans" fo:font-size="10.5pt" fo:language="en" fo:country="US"/></style:default-style>
<style:style style:name="Standard" style:family="paragraph"/>
<style:style style:name="Body" style:family="paragraph" style:parent-style-name="Standard"><style:paragraph-properties fo:margin-top="0.03in" fo:margin-bottom="0.08in" fo:line-height="130%"/></style:style>
<style:style style:name="Li" style:family="paragraph" style:parent-style-name="Standard"><style:paragraph-properties fo:margin-top="0.02in" fo:margin-bottom="0.05in" fo:line-height="130%"/></style:style>
<style:style style:name="Title" style:family="paragraph" style:parent-style-name="Standard"><style:paragraph-properties fo:text-align="center" fo:margin-top="0.1in" fo:margin-bottom="0.05in"/><style:text-properties fo:font-size="28pt" fo:font-weight="bold" fo:color="{NAVY}"/></style:style>
<style:style style:name="Subtitle" style:family="paragraph" style:parent-style-name="Standard"><style:paragraph-properties fo:text-align="center" fo:margin-bottom="0.15in"/><style:text-properties fo:font-size="14pt" fo:color="#555555"/></style:style>
<style:style style:name="Center" style:family="paragraph" style:parent-style-name="Standard"><style:paragraph-properties fo:text-align="center" fo:margin-bottom="0.04in"/></style:style>
<style:style style:name="H1" style:family="paragraph" style:parent-style-name="Standard" style:default-outline-level="1"><style:paragraph-properties fo:margin-top="0.3in" fo:margin-bottom="0.05in" fo:keep-with-next="always" fo:border-bottom="1.5pt solid {GOLD}" fo:padding-bottom="0.03in"/><style:text-properties fo:font-size="17pt" fo:font-weight="bold" fo:color="{NAVY}"/></style:style>
<style:style style:name="H1Break" style:family="paragraph" style:parent-style-name="H1"><style:paragraph-properties fo:break-before="page"/></style:style>
<style:style style:name="ContentsH" style:family="paragraph" style:parent-style-name="H1" style:default-outline-level="1"><style:paragraph-properties fo:break-before="page"/></style:style>
<style:style style:name="H3" style:family="paragraph" style:parent-style-name="Standard" style:default-outline-level="3"><style:paragraph-properties fo:margin-top="0.16in" fo:margin-bottom="0.04in" fo:keep-with-next="always"/><style:text-properties fo:font-size="12pt" fo:font-weight="bold" fo:color="#7a5a12"/></style:style>
<style:style style:name="Meta" style:family="paragraph" style:parent-style-name="Standard"><style:paragraph-properties fo:margin-bottom="0.1in"/><style:text-properties fo:font-size="9pt" fo:font-style="italic" fo:color="#666666"/></style:style>
<style:style style:name="Toc" style:family="paragraph" style:parent-style-name="Standard"><style:paragraph-properties fo:margin-bottom="0.04in" fo:margin-left="0.15in"/></style:style>
<style:style style:name="Check" style:family="paragraph" style:parent-style-name="Standard"><style:paragraph-properties fo:margin-bottom="0.05in" fo:margin-left="0.3in" fo:text-indent="-0.25in" fo:line-height="130%"/></style:style>
<style:style style:name="NoteInfo" style:family="paragraph" style:parent-style-name="Standard"><style:paragraph-properties fo:margin-top="0.06in" fo:margin-bottom="0.1in" fo:padding="0.07in" fo:background-color="#e8eefb" fo:border-left="3pt solid {NAVY}" fo:line-height="125%"/></style:style>
<style:style style:name="NoteTip" style:family="paragraph" style:parent-style-name="NoteInfo"><style:paragraph-properties fo:background-color="#e3f4ea" fo:border-left="3pt solid #1e7d4f"/></style:style>
<style:style style:name="NoteWarn" style:family="paragraph" style:parent-style-name="NoteInfo"><style:paragraph-properties fo:background-color="#fff1d6" fo:border-left="3pt solid #a25f00"/></style:style>
<style:style style:name="THP" style:family="paragraph" style:parent-style-name="Standard"><style:text-properties fo:font-weight="bold" fo:color="#ffffff" fo:font-size="9.5pt"/></style:style>
<style:style style:name="TDP" style:family="paragraph" style:parent-style-name="Standard"><style:paragraph-properties fo:margin-top="0.02in" fo:margin-bottom="0.02in"/><style:text-properties fo:font-size="9.5pt"/></style:style>
<style:style style:name="B" style:family="text"><style:text-properties fo:font-weight="bold"/></style:style>
<style:style style:name="Muted" style:family="text"><style:text-properties fo:color="#777777" fo:font-size="9pt"/></style:style>
<style:style style:name="Code" style:family="text"><style:text-properties style:font-name="Liberation Mono" fo:font-size="9.5pt" fo:background-color="{CREAM}"/></style:style>
<text:list-style style:name="ListBullet"><text:list-level-style-bullet text:level="1" text:bullet-char="•"><style:list-level-properties text:list-level-position-and-space-mode="label-alignment"><style:list-level-label-alignment text:label-followed-by="listtab" text:list-tab-stop-position="0.3in" fo:text-indent="-0.2in" fo:margin-left="0.3in"/></style:list-level-properties></text:list-level-style-bullet></text:list-style>
<text:list-style style:name="ListNumber"><text:list-level-style-number text:level="1" style:num-suffix="." style:num-format="1"><style:list-level-properties text:list-level-position-and-space-mode="label-alignment"><style:list-level-label-alignment text:label-followed-by="listtab" text:list-tab-stop-position="0.35in" fo:text-indent="-0.3in" fo:margin-left="0.35in"/></style:list-level-properties></text:list-level-style-number></text:list-style>
</office:styles>
<office:automatic-styles>
<style:page-layout style:name="pm1"><style:page-layout-properties fo:page-width="8.5in" fo:page-height="11in" fo:margin-top="0.7in" fo:margin-bottom="0.5in" fo:margin-left="0.9in" fo:margin-right="0.9in"/><style:footer-style><style:header-footer-properties fo:min-height="0.3in" fo:margin-top="0.15in"/></style:footer-style></style:page-layout>
<style:style style:name="Footer" style:family="paragraph"><style:paragraph-properties fo:text-align="center"/><style:text-properties fo:font-size="8.5pt" fo:color="#666666"/></style:style>
</office:automatic-styles>
<office:master-styles><style:master-page style:name="Standard" style:page-layout-name="pm1"><style:footer><text:p text:style-name="Footer">{escape(g["title"])} · Training and SOP · Version {g["version"]} · Page <text:page-number text:select-page="current"/></text:p></style:footer></style:master-page></office:master-styles>
</office:document-styles>"""
    meta = (f'<?xml version="1.0" encoding="UTF-8"?><office:document-meta {NS} office:version="1.2"><office:meta><dc:title>{escape(g["title"])}: Training and SOP</dc:title>'
            f'<dc:subject>{escape(g["subtitle"])}</dc:subject><meta:initial-creator>Globlexus Group</meta:initial-creator><dc:language>en-US</dc:language>'
            f'<meta:creation-date>{g["date"]}T00:00:00</meta:creation-date></office:meta></office:document-meta>')
    manifest = ('<?xml version="1.0" encoding="UTF-8"?><manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.2">'
                '<manifest:file-entry manifest:full-path="/" manifest:version="1.2" manifest:media-type="application/vnd.oasis.opendocument.text"/>'
                '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
                '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
                '<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>'
                + ('<manifest:file-entry manifest:full-path="Pictures/logo.png" manifest:media-type="image/png"/>' if logo_png else "") + "</manifest:manifest>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text")          # must be first and stored, not compressed
        for name, data in (("content.xml", content), ("styles.xml", styles), ("meta.xml", meta), ("META-INF/manifest.xml", manifest)):
            z.writestr(name, data, zipfile.ZIP_DEFLATED)
        if logo_png:
            z.writestr("Pictures/logo.png", logo_png, zipfile.ZIP_DEFLATED)
    return buf.getvalue()
