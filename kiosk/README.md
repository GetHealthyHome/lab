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
| `reader.py` | QR / Code 128 decode (zxing-cpp) and a debouncer so one hold-up is one scan | K1 |
| `spots.py` | Full / empty / unclear per shelf spot, and the camera-vs-records alert table | C3, C4 |
| `app.py` | FastAPI JSON API (`/api/session`, `/scan`, `/finish`, `/health`) | |

## API
- `POST /api/session` `{id_code, action: "out"|"in"}` starts a session.
- `POST /api/session/scan` `{code}` adds a tool. Refusals come back as HTTP 409 `{reason}` with text ready for the screen.
- `POST /api/session/finish` saves it; returns include `return_to` for each tool.
- `GET /api/session` gives the open session, `seconds_left` for the countdown bar, and `auto_saved` when the idle timer saved one.
- `DELETE /api/session` cancels.

## Not built yet
Google Sheets/Drive sink, the Pi camera and USB cage camera drivers, the reader service that feeds scans to the API, the cage monitor loop (C1, C5–C7), instructor PIN mode and the rest of K7–K17, systemd units.
