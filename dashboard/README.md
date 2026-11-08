# Lab Toolbox staff dashboard

A Google Apps Script web app that lives inside the Lab Toolbox Sheet. The Pi writes the logbook (Transactions, Snapshots, Alerts and the rest); this page lets staff read it and answer it. Think of the Sheet as the shop's ledger and this page as the front desk where staff look things up and sign for them: nobody has to touch the ledger itself.

| Page | What staff do there | PRD |
| --- | --- | --- |
| Out now | Tools out, who has them, for how long, due time; overdue in red | D1, K16 |
| Alerts | Open alerts with cage and gate photos and the last borrower; acknowledge, resolve, false alarm (with a note); "Record it as returned" for a tool put back without scanning | D2, D7 |
| Search | A student or a tool: full history with gate photos and damage reports | D3 |
| Nightly check | Every spot, pre-filled with the camera's latest view; confirm or note each one and sign off | D4 |
| Day report | Printable end-of-day exceptions | D5 |
| Quarantine | Damage reports and photos; release the tool or retire it | D11 |
| Labels | Worn labels; print a batch (CSV for the label printer) and mark them printed | D12 |
| Records | Tools, People, Spots, battery platforms, kits and kit contents; PIN reset | D6, D7, D13 |
| Calibrate | Ask the Pi for a reference photo, draw a box around each spot's tool, link the spot to its tool (supervisors) | C2 |
| Settings | Loan length, closing time, grace period, PIN on/off, snapshot interval, alert emails, kiosk screen light or dark | D14 |
| Emails | A missing-tool email to `alert_email` within 5 minutes of the alert reaching the Sheet; a weekday digest to `digest_email` at `digest_hour` (default 7 AM): tools out, open alerts, overdue with borrower, missing tools, new damage and quarantine with photo links, labels to reprint, last night's sign-off | D9, D10 |
| Kiosk | Last sync, queue size, recent snapshots; ask for a cage snapshot now | D8, C1 |

Every change made here writes one AuditLog row per changed cell: who, when, old value, new value and why (D7). Editing an existing row needs a reason. Rows in Tools, People, Spots and Kits are retired, never deleted, because records and history point at them by their key.

## Who can use it
Staff are People rows with an `email` and a `role` of `instructor` or `supervisor` (and `active` not set to no). Instructors can do everything except change Records, Settings and PINs, which are supervisor-only. The Google account that deploys the script is always treated as a supervisor, so a new Sheet can be set up before anyone's email is in it.

This assumes staff sign in with accounts in your Google Workspace domain (the PRD's first open question). If staff use personal Google accounts instead, change `webapp.access` in `appsscript.json` to `ANYONE` and `executeAs` to `USER_ACCESSING`, and share the Sheet with each staff member; the rest works the same.

## Install
1. Open the Lab Toolbox Sheet, then **Extensions > Apps Script**.
2. In the editor, open **Project Settings** and tick **Show "appsscript.json" manifest file in editor**. Paste this folder's `appsscript.json` over it. Set `timeZone` to the lab's time zone: it decides what "today" means for the nightly check, the day report and the morning digest.
3. Create the script files `Code`, `Logic`, `Mail` and the HTML file `Index`, and paste in `Code.gs`, `Logic.gs`, `Mail.gs` and `Index.html`. (Or use [clasp](https://github.com/google/clasp): `clasp clone <script id>`, copy these files in, `clasp push`.)
4. **Deploy > New deployment > Web app**. Execute as: **Me**. Who has access: **Anyone within your domain**. Open the URL it gives you and bookmark it for staff.
5. On the dashboard's **Settings** page, fill in `alert_email` and `digest_email`, then press **Turn on emails** once (Google asks you to allow sending mail). Emails are sent from the deploying account. Each alert is emailed once; the Alerts tab's `emailed_at` column records when.
6. Give staff **Viewer** access to the Drive photo folder so the photo thumbnails load. Photos stay private to staff (PRD Privacy).

After pasting new versions of the files, use **Deploy > Manage deployments > Edit > New version** so the bookmarked URL serves them.

## Tabs it uses
Missing tabs read as empty, so the page works before the Pi has written anything. Tabs the dashboard writes (Reconciliations, AuditLog, Settings) are created with their headers on first use.

`Reconciliations`: date, instructor, spots_confirmed, exceptions, signed_at, details (the per-spot answers as JSON). `Health`: written by the Pi's sync service after every push. `References`: reference photos the Pi took for calibration (reference_id, ts, reference_photo, width, height). `Extensions`: loan extensions (txn_id, due_at, by, ts, reason), written by the kiosk.

## Tests
The `.gs` files run in Node against a fake Sheet, so no Google account is needed:
```bash
node --test dashboard/test/*.test.js
```
