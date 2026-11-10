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
`curl -X POST localhost:8000/api/pin -H 'Content-Type: application/json' -d '{"pin":"1234"}'` after giving P-1 a PIN
(`python -m lab_toolbox.pins P-1`).

## The screen
One lean page (`static/index.html`, no libraries) that only talks to the API below and asks it for the state once a second.
Every event gets a color, words and (from the Pi's buzzer) a sound: green outline for a good scan, a red flash with the
reason for a refusal, an amber border with the needed and scanned battery side by side, a red lock screen with a countdown.
Dark by default; a supervisor can switch it to light with `screen_theme` on the dashboard's Settings page (the kiosk
picks it up within 10 minutes). Buttons are at least 80 px (15 mm on the 7" screen); a normal checkout needs no typing. The on-screen keyboard only appears
for instructor jobs and for finding a tool to report that isn't in your list.

Screens: start (Check out / Return, Report a problem, Instructor) → PIN pad → scanning list with a 45 s countdown bar, a
"Scan QR code instead" button and a big Done → summary (due time, or a green RETURN TO banner per tool). Report a problem
works from the start screen and from a session (damaged, not in its spot, worn label). Instructor mode: start a session for
a student, type an asset number, give someone a PIN, link an NFC sticker to a tool, move due times, release a locked tool.

**PIN login and NFC stickers:** the PIN alone says who you are: 4 digits for students, 6 for instructors and supervisors,
and no two people share one. The kiosk hands them out: Instructor → Give a PIN → pick the person, and the new PIN shows
once on screen (also how a forgotten PIN is replaced). Tools are tapped on the NFC reader; each tool tapped out gets its own
photo of the person, taken at that moment. A printed QR label still works: **Scan QR code instead** makes the gate camera
read labels for the next minute. To link a sticker: Instructor → pick the tool → Link NFC sticker → tap the sticker. Any NFC
sticker works, since only its built-in serial number is read.

**First instructor PIN** (nobody can open instructor mode yet), on the Pi's keyboard (Ctrl+Alt+F2 to log in):
```bash
cd /opt/lab-toolbox/kiosk && sudo -u kiosk env KIOSK_DB=/data/kiosk.db .venv/bin/python -m lab_toolbox.pins P-9
```
with the person's `person_id` from the People tab. It prints the PIN. PINs are kept as an HMAC with a key in
`/data/pin.key`; if that file is lost, everyone needs a new PIN.

**NFC reader wiring** (PN532 board, switches set to I2C): VCC to pin 1 (3.3 V), GND to pin 6, SDA to pin 3, SCL to pin 5.
`NFC_READER=pn532` in `/data/lab-toolbox.env` turns it on. `sudo i2cdetect -y 1` should show `24`.

**Tool photos:** once a tool is labeled, an instructor taps **Take tool photos**, picks the tool and holds it up to the
gate camera. The photo stays on the Pi (the screen must work offline) and a copy goes to Drive, with its link in a `photo`
column on the Tools tab. It then shows as a thumbnail in the scanning list and beside the RETURN TO banner. Tools without
a photo just show their name.

**Logo:** the toolbox from the Lab Toolbox logo ships as `lab_toolbox/static/lab-mark.png` (clear background) and sits in
the top bar of every screen next to the name, so it blends into both looks. To use a different logo without a code change,
put it at `/data/logo.png` on the Pi (`install.sh` links `static/logo.png` there); it wins over the shipped one.

**Look:** the screen follows Ande's mockups and design notes: graphite dark by default, light via the `screen_theme` setting.
Hanken Grotesk, JetBrains Mono and the Material Symbols icons used ship in `static/fonts` (SIL OFL / Apache 2.0), so no internet is needed.
The start screen is the PIN keypad with a Check out / Return switch; "Need help?" in the top bar opens problem reports and the instructor login.

## What's here
| Module | Job | PRD |
| --- | --- | --- |
| `db.py` | SQLite schema and migrations; WAL, synchronous NORMAL, foreign keys, 5 s busy timeout; append-only triggers on transactions, snapshots and audit log; `tool_state` view derives "out" from the latest transaction | Data model, Stability |
| `sessions.py` | Checkout and return sessions: several tools per session, a photo of the person per tool tapped out (one per session for returns), rejection rules, put-away spot on return, 45 s idle timeout that saves what was scanned. Also PIN login, instructor mode, NFC sticker linking, battery pairing, kits, damage and problem reports, worn labels | K2–K9, K11–K15, K17 |
| `pins.py` | PIN hashes (HMAC, key on the Pi), unique random PINs (4 digits students, 6 staff), the first-PIN command | K11 |
| `nfc.py` | PN532 NFC reader on I2C: reads a sticker's UID | |
| `loans.py` | Due time (earlier of loan length or closing time), grace period, overdue list, overdue hold | K16 |
| `sync.py` | Outbox queued in the same DB transaction as the record; sends oldest first, stops at the first failure, deletes a local photo once sent | K10 |
| `google.py` | Google side of sync: pushes queued rows to Sheet tabs (matched by header name, new columns added), photos to Drive (each Sheet row gets the Drive link, retries never upload twice), and pulls Spots, Tools and People back down all-or-nothing | K10, Sync to Google |
| `sync_service.py` | Separate process: pushes every 30 s, pulls every 10 min, survives being offline | |
| `reader.py` | QR / Code 128 decode (zxing-cpp), a debouncer so one hold-up is one scan, and `Watcher`, which only decodes after motion (low frame rate when idle, full rate for 5 s after motion or a code) | K1, Camera load |
| `hardware.py` | Gate camera (picamera2: 640x480 grey stream for codes, 1280x960 for the session photo, continuous autofocus on Camera Module 3), USB cage webcam (`fswebcam`), GPIO buzzer with the PRD's sounds | K1, K4, C1 |
| `spots.py` | Full / empty / unclear per shelf spot, and the camera-vs-records alert table | C3, C4 |
| `cage.py` | Cage monitor, its own process: snapshots on the Settings schedule, after returns, on dashboard request; two-in-a-row rule; one open alert per spot, updated not repeated; alert photo with the spot boxed in red | C1, C5–C7 |
| `app.py` | FastAPI JSON API (`/api/session`, `/pin`, `/scan`, `/finish`, `/code`, `/qr`, `/mode`, `/health`); with a gate camera or NFC reader it also runs their read loops | K1, D8 |

## API
- `POST /api/pin` `{pin}` logs in: starts the session chosen with `/api/mode`, or (after Instructor) logs an instructor in. Five wrong PINs in a row lock the keypad for a minute (`locked`, with `until`) and open an `id_locked` alert.
- `POST /api/session/scan` `{code}` adds a tool. Refusals come back as HTTP 409 `{reason}` with text ready for the screen.
- `POST /api/session/finish` saves it; returns include `return_to` for each tool.
- `GET /api/session` gives the open session (with kit piece names and, for a tool still waiting for a battery, which batteries fit and where they sit), `seconds_left` for the countdown bar, `auto_saved` when the idle timer saved one, and `last_event` (`{seq, ok, text}`) for the latest scan so the screen can flash green or red, and `qr` while the camera is reading printed labels.
- `POST /api/mode` `{action: "out"|"in"|"instructor"}`: the Check out / Return / Instructor buttons. The next PIN does that, then it goes back to "out".
- `POST /api/code` `{code}`: one tool code (an NFC sticker UID or a printed label), routed the way an NFC tap or camera scan is, with sound. A USB barcode scanner can use this too.
- `POST /api/qr`: the "Scan QR code instead" button; the gate camera reads printed labels for 60 s.
- `DELETE /api/session` cancels.
- `POST /api/session/remove` `{asset_id}` takes a tool back off the list. `POST /api/session/kit` `{kit_id, missing}` confirms a kit's pieces on return.
- `POST /api/damage` `{asset_id, severity, tags, notes, photo}`, `POST /api/problem` `{asset_id, notes}` (tool not in its spot), `POST /api/label` `{asset_id, condition}` work from any screen. `asset_id` takes the engraved number or the label code; `GET /api/tools?q=` finds a tool by name.
- `GET /api/loans` lists tools out with `due_at` and `overdue`.
- Instructor mode: tap Instructor (`POST /api/mode {action: "instructor"}`, also during a student's session), then `POST /api/pin`. Then `POST /api/instructor/start` `{person_id, action}` (student without their PIN), `/manual` `{asset_id}` (label won't scan), `/pin` `{person_id}` (new random PIN, returned once), `/link_tag` `{asset_id}` (the next NFC sticker tapped becomes this tool's), `/extend` `{asset_id, due_at}`, `/clear` `{asset_id, note}` (release from quarantine), `/logout`; `GET /api/instructor/people?q=`. Every one is logged in AuditLog with the instructor's name. An instructor who walks away is logged out after 90 seconds.

Refusals are HTTP 409 `{reason, kind}`; `kind` is `rejected` (red flash), `wrong_battery` (amber, with `required` and `scanned`) or `locked` (lock screen, with `until`), and the buzzer plays the matching sound.

How the new rules behave:
- **PIN** (K11): always on; it is how people log in. A supervisor's PIN reset on the dashboard clears it until an instructor gives a new one at the kiosk.
- **Batteries** (K12): a tool with `needs_battery` can't be saved until a battery of its `battery_group_id` is scanned. The scan reply lists which batteries fit and where the available ones sit. If the session times out, what was scanned is saved anyway: it left the cage.
- **Kits** (K13): scanning a kit's case code adds every available piece (standard) or asks for each piece to be scanned (verified). On return, the student confirms the pieces; anything missing stays checked out, the kit is marked incomplete and a `kit_incomplete` alert opens.
- **Damage** (K14): Needs inspection or Safety hazard quarantines the tool. It can still be returned, but not checked out until an instructor releases it (here or on the dashboard).
- **Nightly database copy**: with the personal Drive sign-in (`drive_login`), the sync service copies the whole database to that person's Drive once a day, just after midnight, into a private folder named `Lab Toolbox backups` (not the photo folder, which staff can see: the copy holds PIN hashes). Seven files, `kiosk-Mon.db` to `kiosk-Sun.db`, each replaced a week later. To restore, download one and put it on the Pi as `/data/kiosk.db` with the services stopped. Without that sign-in only the hourly local copies in `/data/backups` exist.
- **Overnight dimming**: from `lab_close` until `lab_open` (default 07:00; add a `lab_open` row to the Settings tab to change it) the screen dims to a tenth of full brightness once nobody has touched it or scanned for 5 minutes. A touch or a scan brightens it at once. It works on screens with a file under `/sys/class/backlight` (the official Pi touchscreen); other screens are left alone. An already-installed Pi needs `sudo cp kiosk/deploy/99-lab-toolbox-backlight.rules /etc/udev/rules.d/` and a reboot.
- **Loans** (K16): due at the earlier of `loan_hours` (default 4) after checkout or `lab_close` (default 21:00, Pi local time); overdue after `grace_minutes` (default 15) more. With `hold_overdue` on (default), a student with an overdue tool can't start a checkout unless an instructor starts it for them.

## Connect to Google
Like a mail carrier with a key to one mailbox: the Pi gets its own Google robot account that can only open the one Sheet and one folder you share with it.

1. In Google Cloud console: create a project, enable the **Google Sheets API** and **Google Drive API**, create a **service account**, and download its JSON key. Keep the key only on the Pi (for example `/data/google-key.json`, readable by the kiosk user only).
2. Create the Sheet with tabs `Transactions`, `Spots`, `Tools`, `People`, `Settings`, `Snapshots`, `Alerts`, `AuditLog`. Every one of these must exist, even empty, or the pull fails. `BatteryGroups`, `Kits` and `KitItems` are read when they exist; `References`, `Health`, `DamageReports`, `ReprintQueue` and `Extensions` are created by the first row the kiosk sends. Share it with the service account's email as **Editor**.
3. Put the photo folder in a **Shared Drive** and add the service account as a **Content manager**. A service account has no storage of its own, so uploads to a normal "My Drive" folder are refused. **No Shared Drive** (a personal Google account): skip the folder. In the same Cloud project set up the OAuth consent screen (External, published **In production** so the sign-in doesn't lapse after 7 days), create an OAuth client of type **TVs and Limited Input devices**, and put its downloaded JSON on the Pi as `/data/google-client.json`. Then run `sudo -u kiosk /opt/lab-toolbox/kiosk/.venv/bin/python -m lab_toolbox.drive_login` once: it shows a code to type in at google.com/device, makes a "Lab Toolbox photos" folder in that person's Drive and saves the sign-in to `/data/google-user.json`. The sync service then uploads photos as that person (it can only touch files it made) and ignores `DRIVE_FOLDER_ID`; the Sheet is still written by the service account.
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

Install on a Pi 3B+ with a fresh **Raspberry Pi OS Lite (64-bit)** card (16 GB or more, high-endurance):
```bash
sudo apt install -y git
sudo git clone https://github.com/GetHealthyHome/lab /opt/lab-toolbox
sudo /opt/lab-toolbox/kiosk/deploy/install.sh     # reboots once to make the data partition
sudo /opt/lab-toolbox/kiosk/deploy/install.sh     # run again after the reboot; reboots into the kiosk screen
```
What `deploy/install.sh` sets up:
- **Data partition.** The first run shrinks the system partition to 8 GB and turns the rest of the card into `/data` (label `LTDATA`). It does this from the initramfs on the next boot, before the system is mounted, because a mounted disk can't be shrunk. `/data` holds everything that changes: `kiosk.db`, `photos/`, `lab-toolbox.env` (the settings file every service reads), `google-key.json`, `logo.png`, the system logs (`journal/`) and hourly database copies (`backups/`, 24 rotating files).
- **Services**, each restarting on its own if it stops: `lab-toolbox-kiosk` (API, gate camera, buzzer), `lab-toolbox-sync`, `lab-toolbox-cage`, `lab-toolbox-screen` and the `lab-toolbox-backup` timer. Each one waits for `/data`.
- **The screen.** `lab-toolbox-screen` runs Chromium full screen inside `cage`, a display server that shows one window and nothing else: no address bar, no desktop, no switching to a text console. A Chromium policy (`deploy/chromium-policy.json`) blocks every address except the kiosk's own page and turns off developer tools, downloads and printing, so even a plugged-in keyboard can't wander off.
- **Lighter boot.** Bluetooth, modem manager and the apt and man-db timers are off; swap is in compressed RAM, not on the card; the hardware watchdog reboots a frozen Pi within 15 s.

**Lock it read-only** once it's set up and tested. Like laminating a sign: the system can't be scribbled on, so a power cut can't corrupt it, while `/data` stays writable. Changes to anything outside `/data` vanish at the next reboot.
```bash
sudo raspi-config nonint do_overlayfs 0 && sudo reboot    # lock
sudo raspi-config nonint do_overlayfs 1 && sudo reboot    # unlock, to update:
cd /opt/lab-toolbox && sudo git pull && sudo kiosk/deploy/install.sh   # then lock again
```
Logs: `journalctl -u lab-toolbox-kiosk` (or `-sync`, `-cage`, `-screen`).

Buzzer: a passive piezo (or a small amp and speaker) between `BUZZER_PIN` (BCM numbering, default 18) and ground. The motion and timing values at the top of `reader.py` are starting points to tune on the real camera.

## Cage monitor
Like a guard who walks past the shelves on a timer. It takes a snapshot:

- every `snapshot_interval_hours` hours, set in the Sheet's **Settings** tab (default 1; `0.5` works for every 30 minutes). Changes reach the Pi on the next pull, within 10 minutes.
- 2 minutes after each return session, so the tools have time to go back on their hooks.
- when the dashboard writes any new value into the `snapshot_request` setting (a timestamp is easiest). It runs after the next pull, so within about 10 minutes.
- 1 minute after any snapshot that saw a problem. An alert only opens once two snapshots in a row agree (C7), so a hand reaching in is ignored.

Spots need a `box` (`[x, y, w, h]` in reference-photo pixels) and a `reference_photo`; spots without them are skipped until they are calibrated. Calibration (C2) happens on the dashboard's **Calibrate** page: it asks the Pi for a reference photo (the `reference_request` setting), the Pi takes one, keeps it in `PHOTO_DIR/references/` and sends a copy to Drive and the `References` tab, and the supervisor draws each spot's box on it. The Sheet stores the photo's Drive link; the Pi swaps it back for its own copy on the next pull. Retake the reference photo whenever the lighting or the camera changes. If the webcam fails, every spot reads "unclear" and turns into Check camera alerts. Two cage cameras: set `CAGE_CAMERA` to both device names with a comma between (use the fixed names under `/dev/v4l/by-id/`, since `video0` and `video2` can swap at boot). The Pi stacks the two views into one tall picture, first camera on top, so calibration, snapshots and alerts work exactly as with one camera; if either camera fails, the whole snapshot counts as failed. Run it on the Pi with `systemctl enable --now lab-toolbox-cage` (unit in `deploy/`), or on a bench with `KIOSK_DB=kiosk.db CAGE_CAMERA=/dev/video0 python -m lab_toolbox.cage`.

## Not built yet
Deleting photos after the retention period.
