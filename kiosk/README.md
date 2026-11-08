# Lab Toolbox kiosk

The Raspberry Pi service at the cage gate (see the Lab Toolbox PRD). It runs fully offline: every checkout is saved in local SQLite first and queued for Google later.

## Run it
```bash
cd kiosk
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
python -m lab_toolbox.seed kiosk.db          # demo IDs ID-1001, ID-1002, instructor ID-9001; tools T-0001..T-0006, kit K-0001
KIOSK_DB=kiosk.db uvicorn lab_toolbox.app:app --port 8000
pytest
```
Open http://localhost:8000 in a window sized 800x480 (the official 7" screen). With no camera on a bench, "scan" a code with
`curl -X POST localhost:8000/api/code -H 'Content-Type: application/json' -d '{"code":"ID-1001"}'`.

## The screen
One lean page (`static/index.html`, no libraries) that only talks to the API below and asks it for the state once a second.
Every event gets a color, words and (from the Pi's buzzer) a sound: green outline for a good scan, a red flash with the
reason for a refusal, an amber border with the needed and scanned battery side by side, a red lock screen with a countdown.
Dark by default; a supervisor can switch it to light with `screen_theme` on the dashboard's Settings page (the kiosk
picks it up within 10 minutes). Buttons are at least 80 px (15 mm on the 7" screen); a normal checkout needs no typing. The on-screen keyboard only appears
for instructor jobs and for finding a tool to report that isn't in your list.

Screens: start (Check out / Return, Report a problem, Instructor) → PIN pad → scanning list with a 45 s countdown bar and a big
Done → summary (due time, or a green RETURN TO banner per tool). Report a problem works from the start screen and from a
session (damaged, not in its spot, worn label). Instructor mode: start a session for a student, type an asset number, link a
new badge, move due times, release a locked tool.

**Tool photos:** once a tool is labeled, an instructor taps **Take tool photos**, picks the tool and holds it up to the
gate camera. The photo stays on the Pi (the screen must work offline) and a copy goes to Drive, with its link in a `photo`
column on the Tools tab. It then shows as a thumbnail in the scanning list and beside the RETURN TO banner. Tools without
a photo just show their name.

**Logo:** put the lab's logo at `lab_toolbox/static/logo.png` (square, transparent background, about 400 px) and restart the
service. It shows large on the start screen and small in the corner of every other screen. Until then a dashed "LAB LOGO"
placeholder (`static/logo.svg`) holds the spot.

## What's here
| Module | Job | PRD |
| --- | --- | --- |
| `db.py` | SQLite schema and migrations; WAL, synchronous NORMAL, foreign keys, 5 s busy timeout; append-only triggers on transactions, snapshots and audit log; `tool_state` view derives "out" from the latest transaction | Data model, Stability |
| `sessions.py` | Checkout and return sessions: several tools per session, one gate photo per session, rejection rules, put-away spot on return, 45 s idle timeout that saves what was scanned. Also PIN prompts, instructor mode, battery pairing, kits, damage and problem reports, worn labels | K2–K9, K11–K15, K17 |
| `pins.py` | Argon2id PIN hashes; 5 wrong tries lock the ID for 15 minutes | K11 |
| `loans.py` | Due time (earlier of loan length or closing time), grace period, overdue list, overdue hold | K16 |
| `sync.py` | Outbox queued in the same DB transaction as the record; sends oldest first, stops at the first failure, deletes a local photo once sent | K10 |
| `google.py` | Google side of sync: pushes queued rows to Sheet tabs (matched by header name, new columns added), photos to Drive (each Sheet row gets the Drive link, retries never upload twice), and pulls Spots, Tools and People back down all-or-nothing | K10, Sync to Google |
| `sync_service.py` | Separate process: pushes every 30 s, pulls every 10 min, survives being offline | |
| `reader.py` | QR / Code 128 decode (zxing-cpp), a debouncer so one hold-up is one scan, and `Watcher`, which only decodes after motion (low frame rate when idle, full rate for 5 s after motion or a code) | K1, Camera load |
| `hardware.py` | Gate camera (picamera2: 640x480 grey stream for codes, 1280x960 for the session photo, continuous autofocus on Camera Module 3), USB cage webcam (`fswebcam`), GPIO buzzer with the PRD's sounds | K1, K4, C1 |
| `spots.py` | Full / empty / unclear per shelf spot, and the camera-vs-records alert table | C3, C4 |
| `cage.py` | Cage monitor, its own process: snapshots on the Settings schedule, after returns, on dashboard request; two-in-a-row rule; one open alert per spot, updated not repeated; alert photo with the spot boxed in red | C1, C5–C7 |
| `app.py` | FastAPI JSON API (`/api/session`, `/scan`, `/finish`, `/code`, `/mode`, `/health`); with a gate camera it also runs the scan loop | K1, D8 |

## API
- `POST /api/session` `{id_code, action: "out"|"in"}` starts a session.
- `POST /api/session/scan` `{code}` adds a tool. Refusals come back as HTTP 409 `{reason}` with text ready for the screen.
- `POST /api/session/finish` saves it; returns include `return_to` for each tool.
- `GET /api/session` gives the open session (with kit piece names and, for a tool still waiting for a battery, which batteries fit and where they sit), `seconds_left` for the countdown bar, `auto_saved` when the idle timer saved one, and `last_event` (`{seq, ok, text}`) for the latest camera scan so the screen can flash green or red.
- `POST /api/mode` `{action: "out"|"in"}`: the Check out / Return buttons. The next ID scan starts that kind of session, then it goes back to "out".
- `POST /api/code` `{code}`: one code, routed the way a camera scan is (an ID with no session open, otherwise a tool), with sound. A USB barcode scanner can use this too.
- `DELETE /api/session` cancels.
- `POST /api/pin` `{pin}` answers the PIN prompt on screen: a student's (`session.pin` is `check`, or `setup` the first time, when they choose one) or an instructor logging in.
- `POST /api/session/remove` `{asset_id}` takes a tool back off the list. `POST /api/session/kit` `{kit_id, missing}` confirms a kit's pieces on return.
- `POST /api/damage` `{asset_id, severity, tags, notes, photo}`, `POST /api/problem` `{asset_id, notes}` (tool not in its spot), `POST /api/label` `{asset_id, condition}` work from any screen. `asset_id` takes the engraved number or the label code; `GET /api/tools?q=` finds a tool by name.
- `GET /api/loans` lists tools out with `due_at` and `overdue`.
- Instructor mode: tap Instructor (`POST /api/mode {action: "instructor"}`) or scan an instructor badge during a student's session, then `POST /api/pin`. Then `POST /api/instructor/start` `{person_id, action}` (ID won't scan), `/manual` `{asset_id}` (label won't scan), `/enroll` `{person_id}` (the next scanned card becomes theirs), `/extend` `{asset_id, due_at}`, `/clear` `{asset_id, note}` (release from quarantine), `/logout`; `GET /api/instructor/people?q=`. Every one is logged in AuditLog with the instructor's name. An instructor who walks away is logged out after 90 seconds.

Refusals are HTTP 409 `{reason, kind}`; `kind` is `rejected` (red flash), `wrong_battery` (amber, with `required` and `scanned`) or `locked` (lock screen, with `until`), and the buzzer plays the matching sound.

How the new rules behave:
- **PIN** (K11): on unless the `require_pin` setting is `no`. A person with no PIN chooses one on their next scan; a supervisor's PIN reset on the dashboard clears it so they choose again. Five wrong tries lock that ID for 15 minutes and open an `id_locked` alert.
- **Batteries** (K12): a tool with `needs_battery` can't be saved until a battery of its `battery_group_id` is scanned. The scan reply lists which batteries fit and where the available ones sit. If the session times out, what was scanned is saved anyway: it left the cage.
- **Kits** (K13): scanning a kit's case code adds every available piece (standard) or asks for each piece to be scanned (verified). On return, the student confirms the pieces; anything missing stays checked out, the kit is marked incomplete and a `kit_incomplete` alert opens.
- **Damage** (K14): Needs inspection or Safety hazard quarantines the tool. It can still be returned, but not checked out until an instructor releases it (here or on the dashboard).
- **Loans** (K16): due at the earlier of `loan_hours` (default 4) after checkout or `lab_close` (default 21:00, Pi local time); overdue after `grace_minutes` (default 15) more. With `hold_overdue` on (default), a student with an overdue tool can't start a checkout unless an instructor starts it for them.

## Connect to Google
Like a mail carrier with a key to one mailbox: the Pi gets its own Google robot account that can only open the one Sheet and one folder you share with it.

1. In Google Cloud console: create a project, enable the **Google Sheets API** and **Google Drive API**, create a **service account**, and download its JSON key. Keep the key only on the Pi (for example `/data/google-key.json`, readable by the kiosk user only).
2. Create the Sheet with tabs `Transactions`, `Spots`, `Tools`, `People`, `Settings`, `Snapshots`, `Alerts`, `AuditLog`. Every one of these must exist, even empty, or the pull fails. `BatteryGroups`, `Kits` and `KitItems` are read when they exist; `References`, `Health`, `DamageReports`, `ReprintQueue` and `Extensions` are created by the first row the kiosk sends. Share it with the service account's email as **Editor**.
3. Put the photo folder in a **Shared Drive** and add the service account as a **Content manager**. A service account has no storage of its own, so uploads to a normal "My Drive" folder are refused.
4. Header rows: `Spots`: `spot_id, label, box, reference_photo`. `Tools`: `asset_id, qr_code, name, category, spot_id, status, notes` (the kiosk adds `photo`). `People`: `person_id, id_code, name, role, pin_hash, active, email` (staff need `email` for the dashboard). Tools can also have `serial, brand, model, needs_battery, battery_group_id, is_battery`. `BatteryGroups`: `group_id, name`. `Kits`: `kit_id, case_code, name, spot_id, check_mode, status`. `KitItems`: `kit_id, asset_id`. `Settings`: `key, value`. `Alerts`: at least `alert_id, status, resolution_note`. `Transactions`, `Snapshots` and the rest of `Alerts` can start empty: the kiosk writes its header on the first push, and any column order works after that.
5. Run it:
```bash
pip install -e '.[pi]'
KIOSK_DB=/data/kiosk.db GOOGLE_KEY_FILE=/data/google-key.json SHEET_ID=<id from the Sheet URL> \
  DRIVE_FOLDER_ID=<id from the folder URL> python -m lab_toolbox.sync_service
```
Alert rows are updated in place when the cage monitor sees the same problem again; it never overwrites `status`, `resolved_by` or `resolution_note`, which belong to the dashboard. The pull copies each alert's status back, so once staff resolve one, the next problem at that spot opens a fresh alert.

Tools.status in the Sheet is only read for `quarantined` and `retired`; "out" always comes from the kiosk's own transactions.

## On the Pi
The gate camera has one owner: the web service. Reading codes and taking the Done photo both happen there, because only one program can hold the Pi camera at a time (like one person holding the TV remote). Scanning runs beside the API in the same process, decoding on a worker thread so the screen stays quick.

```bash
sudo apt install python3-picamera2 fswebcam      # picamera2 comes from apt on Pi OS, not pip
sudo useradd -r -G video,gpio kiosk
sudo mkdir -p /data && sudo chown kiosk /data
sudo git clone https://github.com/GetHealthyHome/lab /opt/lab-toolbox
cd /opt/lab-toolbox/kiosk
sudo python3 -m venv --system-site-packages .venv   # so the venv sees apt's picamera2
sudo .venv/bin/pip install -e '.[pi]'
sudo cp deploy/lab-toolbox.env.example /etc/lab-toolbox.env   # then fill it in, chmod 600, chown kiosk
sudo cp deploy/*.service /etc/systemd/system/
sudo systemctl enable --now lab-toolbox-kiosk lab-toolbox-sync lab-toolbox-cage
```
Buzzer: a passive piezo (or a small amp and speaker) between `BUZZER_PIN` (BCM numbering, default 18) and ground. The motion and timing values at the top of `reader.py` are starting points to tune on the real camera.

## Cage monitor
Like a guard who walks past the shelves on a timer. It takes a snapshot:

- every `snapshot_interval_hours` hours, set in the Sheet's **Settings** tab (default 1; `0.5` works for every 30 minutes). Changes reach the Pi on the next pull, within 10 minutes.
- 2 minutes after each return session, so the tools have time to go back on their hooks.
- when the dashboard writes any new value into the `snapshot_request` setting (a timestamp is easiest). It runs after the next pull, so within about 10 minutes.
- 1 minute after any snapshot that saw a problem. An alert only opens once two snapshots in a row agree (C7), so a hand reaching in is ignored.

Spots need a `box` (`[x, y, w, h]` in reference-photo pixels) and a `reference_photo`; spots without them are skipped until they are calibrated. Calibration (C2) happens on the dashboard's **Calibrate** page: it asks the Pi for a reference photo (the `reference_request` setting), the Pi takes one, keeps it in `PHOTO_DIR/references/` and sends a copy to Drive and the `References` tab, and the supervisor draws each spot's box on it. The Sheet stores the photo's Drive link; the Pi swaps it back for its own copy on the next pull. Retake the reference photo whenever the lighting or the camera changes. If the webcam fails, every spot reads "unclear" and turns into Check camera alerts. Run it on the Pi with `systemctl enable --now lab-toolbox-cage` (unit in `deploy/`), or on a bench with `KIOSK_DB=kiosk.db CAGE_CAMERA=/dev/video0 python -m lab_toolbox.cage`.

## Not built yet
A service that starts Chromium in kiosk mode, and deleting photos after the retention period.
