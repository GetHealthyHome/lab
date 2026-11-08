# Lab Toolbox kiosk

The Raspberry Pi service at the cage gate (see the Lab Toolbox PRD). It runs fully offline: every checkout is saved in local SQLite first and queued for Google later.

## Run it
```bash
cd kiosk
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
python -m lab_toolbox.seed kiosk.db          # demo IDs ID-1001, ID-1002; tools T-0001, T-0002
KIOSK_DB=kiosk.db uvicorn lab_toolbox.app:app --port 8000
pytest
```
Open http://localhost:8000 for the placeholder screen. It is for bench testing only: the real kiosk UI is not designed yet and will talk to the same API.

## What's here
| Module | Job | PRD |
| --- | --- | --- |
| `db.py` | SQLite schema and migrations; WAL, synchronous NORMAL, foreign keys, 5 s busy timeout; append-only triggers on transactions, snapshots and audit log; `tool_state` view derives "out" from the latest transaction | Data model, Stability |
| `sessions.py` | Checkout and return sessions: several tools per session, one gate photo per session, rejection rules, put-away spot on return, 45 s idle timeout that saves what was scanned | K2–K6, K9 |
| `sync.py` | Outbox queued in the same DB transaction as the record; sends oldest first, stops at the first failure, deletes a local photo once sent | K10 |
| `google.py` | Google side of sync: pushes queued rows to Sheet tabs (matched by header name, new columns added), photos to Drive (each Sheet row gets the Drive link, retries never upload twice), and pulls Spots, Tools and People back down all-or-nothing | K10, Sync to Google |
| `sync_service.py` | Separate process: pushes every 30 s, pulls every 10 min, survives being offline | |
| `reader.py` | QR / Code 128 decode (zxing-cpp), a debouncer so one hold-up is one scan, and `Watcher`, which only decodes after motion (low frame rate when idle, full rate for 5 s after motion or a code) | K1, Camera load |
| `hardware.py` | Gate camera (picamera2: 640x480 grey stream for codes, 1280x960 for the session photo, continuous autofocus on Camera Module 3), USB cage webcam (`fswebcam`), GPIO buzzer with the PRD's sounds | K1, K4, C1 |
| `spots.py` | Full / empty / unclear per shelf spot, and the camera-vs-records alert table | C3, C4 |
| `app.py` | FastAPI JSON API (`/api/session`, `/scan`, `/finish`, `/code`, `/mode`, `/health`); with a gate camera it also runs the scan loop | K1 |

## API
- `POST /api/session` `{id_code, action: "out"|"in"}` starts a session.
- `POST /api/session/scan` `{code}` adds a tool. Refusals come back as HTTP 409 `{reason}` with text ready for the screen.
- `POST /api/session/finish` saves it; returns include `return_to` for each tool.
- `GET /api/session` gives the open session, `seconds_left` for the countdown bar, `auto_saved` when the idle timer saved one, and `last_event` (`{seq, ok, text}`) for the latest camera scan so the screen can flash green or red.
- `POST /api/mode` `{action: "out"|"in"}`: the Check out / Return buttons. The next ID scan starts that kind of session, then it goes back to "out".
- `POST /api/code` `{code}`: one code, routed the way a camera scan is (an ID with no session open, otherwise a tool), with sound. A USB barcode scanner can use this too.
- `DELETE /api/session` cancels.

## Connect to Google
Like a mail carrier with a key to one mailbox: the Pi gets its own Google robot account that can only open the one Sheet and one folder you share with it.

1. In Google Cloud console: create a project, enable the **Google Sheets API** and **Google Drive API**, create a **service account**, and download its JSON key. Keep the key only on the Pi (for example `/data/google-key.json`, readable by the kiosk user only).
2. Create the Sheet with tabs `Transactions`, `Spots`, `Tools`, `People` (plus `Snapshots`, `Alerts`, `AuditLog` later). Share it with the service account's email as **Editor**.
3. Put the photo folder in a **Shared Drive** and add the service account as a **Content manager**. A service account has no storage of its own, so uploads to a normal "My Drive" folder are refused.
4. Header rows: `Spots`: `spot_id, label, box, reference_photo`. `Tools`: `asset_id, qr_code, name, category, spot_id, status, notes`. `People`: `person_id, id_code, name, role, pin_hash, active`. `Transactions` can start empty: the kiosk writes its header on the first push, and any column order works after that.
5. Run it:
```bash
pip install -e '.[pi]'
KIOSK_DB=/data/kiosk.db GOOGLE_KEY_FILE=/data/google-key.json SHEET_ID=<id from the Sheet URL> \
  DRIVE_FOLDER_ID=<id from the folder URL> python -m lab_toolbox.sync_service
```
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
sudo systemctl enable --now lab-toolbox-kiosk lab-toolbox-sync
```
Buzzer: a passive piezo (or a small amp and speaker) between `BUZZER_PIN` (BCM numbering, default 18) and ground. The motion and timing values at the top of `reader.py` are starting points to tune on the real camera.

## Not built yet
The cage monitor loop (C1, C5–C7; the webcam driver is ready), instructor PIN mode and the rest of K7–K17, the wrong-battery and ID-locked sounds, the kiosk screen itself, and a service that starts Chromium in kiosk mode.
