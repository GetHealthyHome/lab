"""Google side of sync: the Sheet is the logbook, the Drive folder is the photo album it points to.

Talks to the Sheets and Drive REST APIs through any requests-like session (google-auth's AuthorizedSession
on the Pi, a fake in tests), so nothing heavier than google-auth is needed on the Pi's 1 GB.
"""
import json
import mimetypes
import os
import sqlite3
from typing import Any
from urllib.parse import quote

from .db import tx

SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

# Outbox kind -> Sheet tab. "photo" goes to Drive instead.
TABS = {"transaction": "Transactions", "snapshot": "Snapshots", "alert": "Alerts", "audit": "AuditLog"}
# Payload fields that hold a local photo path; swapped for the Drive link before the row is written.
PHOTO_FIELDS = ("gate_photo", "photo", "snapshot_photo", "reference_photo")
# Kinds whose rows are updated in place (by this key) instead of appended again (C6).
UPSERT_KEYS = {"alert": "alert_id"}
# Columns the dashboard owns: an update from the kiosk never overwrites them.
DASHBOARD_FIELDS = {"status", "resolved_by", "resolution_note"}
ALERT_STATUSES = ("open", "acknowledged", "resolved", "false_alarm")


class GoogleError(Exception):
    pass


class GoogleSink:
    """Implements sync.Sink. Rows go under the tab's header row, matched by column name, so staff can reorder columns."""

    def __init__(self, session, conn: sqlite3.Connection, sheet_id: str, folder_id: str):
        self.http, self.conn, self.sheet_id, self.folder_id = session, conn, sheet_id, folder_id
        self._headers: dict[str, list[str]] = {}

    @classmethod
    def from_service_account(cls, key_file: str, conn: sqlite3.Connection, sheet_id: str, folder_id: str) -> "GoogleSink":
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(key_file, scopes=SCOPES)
        return cls(AuthorizedSession(creds), conn, sheet_id, folder_id)

    # -- push ---------------------------------------------------------------

    def send(self, kind: str, payload: dict[str, Any]) -> None:
        if kind == "photo":
            self._upload_photo(payload["path"])
            return
        if kind not in TABS:
            raise GoogleError(f"no Sheet tab for queue kind {kind!r}")
        row = dict(payload)
        for f in PHOTO_FIELDS:
            if row.get(f):
                row[f] = self._link_for(row[f])
        self._append(TABS[kind], row, UPSERT_KEYS.get(kind))

    def _upload_photo(self, path: str) -> None:
        if self.conn.execute("SELECT 1 FROM drive_photos WHERE path = ?", (path,)).fetchone():
            return  # uploaded already; the outbox just didn't get to mark it sent
        if not os.path.exists(path):
            # shortcut: a lost local photo is skipped, not retried forever; its rows keep the local path.
            return
        meta = {"name": os.path.basename(path), "parents": [self.folder_id]}
        with open(path, "rb") as f:
            files = {
                "metadata": (None, json.dumps(meta), "application/json"),
                "file": (meta["name"], f, mimetypes.guess_type(path)[0] or "image/jpeg"),
            }
            r = self.http.post(
                DRIVE_UPLOAD,
                params={"uploadType": "multipart", "fields": "id,webViewLink", "supportsAllDrives": "true"},
                files=files,
            )
        body = self._ok(r)
        with tx(self.conn):
            self.conn.execute(
                "INSERT INTO drive_photos (path, file_id, link) VALUES (?, ?, ?)",
                (path, body["id"], body["webViewLink"]),
            )

    def _link_for(self, path: str) -> str:
        row = self.conn.execute("SELECT link FROM drive_photos WHERE path = ?", (path,)).fetchone()
        return row["link"] if row else path

    def _append(self, tab: str, row: dict[str, Any], key: str | None = None) -> None:
        header = self._header(tab)
        missing = [k for k in row if k not in header]
        if missing:  # new field from a later kiosk version: add the column instead of dropping data
            header = header + missing
            self._put(f"{tab}!1:1", [header])
            self._headers[tab] = header
        if key:
            # shortcut: reads the whole tab to find the row; fine for the Alerts tab's size, revisit past ~10k rows.
            k = header.index(key)
            for i, old in enumerate(self._get(tab)[1:], start=2):
                if k < len(old) and str(old[k]) == str(row[key]):
                    old = list(old) + [""] * (len(header) - len(old))
                    values = [old[c] if col in DASHBOARD_FIELDS or col not in row else _cell(row[col])
                              for c, col in enumerate(header)]
                    self._put(f"{tab}!A{i}", [values])
                    return
        values = [_cell(row.get(col)) for col in header]
        r = self.http.post(
            f"{SHEETS}/{self.sheet_id}/values/{quote(tab)}!A1:append",
            params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
            json={"values": [values]},
        )
        self._ok(r)

    def _header(self, tab: str) -> list[str]:
        if tab not in self._headers:
            rows = self._get(f"{tab}!1:1")
            self._headers[tab] = [str(c) for c in rows[0]] if rows else []
        return self._headers[tab]

    # -- pull ---------------------------------------------------------------

    def pull(self) -> dict[str, int]:
        """Copy Spots, Tools, People and Settings down from the Sheet (supervisors edit them there), plus the
        dashboard's answer on each alert. Returns rows per tab."""
        spots, tools, people = self._records("Spots", "spot_id"), self._records("Tools", "asset_id"), self._records("People", "person_id")
        settings, alerts = self._records("Settings", "key"), self._records("Alerts", "alert_id")
        with tx(self.conn):  # all three or none, so a half-read Sheet never leaves tools pointing at missing spots
            for s in spots:
                self.conn.execute(
                    "INSERT INTO spots (spot_id, label, box, reference_photo) VALUES (:spot_id, :label, :box, :reference_photo)"
                    " ON CONFLICT(spot_id) DO UPDATE SET label=excluded.label, box=excluded.box,"
                    " reference_photo=excluded.reference_photo",
                    {"spot_id": s["spot_id"], "label": s.get("label") or s["spot_id"],
                     "box": s.get("box") or None, "reference_photo": s.get("reference_photo") or None},
                )
            for t in tools:
                # Sheet status may say out/missing; locally those come from transactions and alerts, not this column.
                status = t.get("status") if t.get("status") in ("quarantined", "retired") else "available"
                self.conn.execute(
                    "INSERT INTO tools (asset_id, qr_code, name, category, spot_id, status, notes)"
                    " VALUES (:asset_id, :qr_code, :name, :category, :spot_id, :status, :notes)"
                    " ON CONFLICT(asset_id) DO UPDATE SET qr_code=excluded.qr_code, name=excluded.name,"
                    " category=excluded.category, spot_id=excluded.spot_id, status=excluded.status, notes=excluded.notes",
                    {"asset_id": t["asset_id"], "qr_code": t["qr_code"], "name": t["name"],
                     "category": t.get("category") or None, "spot_id": t.get("spot_id") or None,
                     "status": status, "notes": t.get("notes") or None},
                )
            for p in people:
                self.conn.execute(
                    "INSERT INTO people (person_id, id_code, name, role, pin_hash, active)"
                    " VALUES (:person_id, :id_code, :name, :role, :pin_hash, :active)"
                    " ON CONFLICT(person_id) DO UPDATE SET id_code=excluded.id_code, name=excluded.name,"
                    " role=excluded.role, pin_hash=excluded.pin_hash, active=excluded.active",
                    {"person_id": p["person_id"], "id_code": p["id_code"], "name": p["name"],
                     "role": (p.get("role") or "student").lower(), "pin_hash": p.get("pin_hash") or None,
                     "active": _truthy(p.get("active", True))},
                )
            for st in settings:
                self.conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                                  (str(st["key"]), str(st.get("value", ""))))
            for a in alerts:  # so the cage monitor opens a fresh alert once staff resolve the old one
                status = str(a.get("status") or "").strip().lower().replace(" ", "_")
                if status in ALERT_STATUSES:
                    self.conn.execute("UPDATE alerts SET status = ?, resolution_note = ? WHERE alert_id = ?",
                                      (status, a.get("resolution_note") or None, a["alert_id"]))
        return {"Spots": len(spots), "Tools": len(tools), "People": len(people), "Settings": len(settings)}

    def _records(self, tab: str, key: str) -> list[dict[str, Any]]:
        rows = self._get(tab)
        if not rows:
            return []
        header = [str(c).strip() for c in rows[0]]
        out = []
        for r in rows[1:]:
            rec = {h: (r[i] if i < len(r) else "") for i, h in enumerate(header)}
            if str(rec.get(key, "")).strip():  # skip blank rows staff leave between entries
                out.append({k: (v.strip() if isinstance(v, str) else v) for k, v in rec.items()})
        return out

    # -- http -----------------------------------------------------------------

    def _get(self, rng: str) -> list[list[Any]]:
        r = self.http.get(f"{SHEETS}/{self.sheet_id}/values/{quote(rng)}", params={"valueRenderOption": "UNFORMATTED_VALUE"})
        return self._ok(r).get("values", [])

    def _put(self, rng: str, values: list[list[Any]]) -> None:
        r = self.http.put(f"{SHEETS}/{self.sheet_id}/values/{quote(rng)}", params={"valueInputOption": "RAW"}, json={"values": values})
        self._ok(r)

    @staticmethod
    def _ok(r) -> dict:
        if r.status_code >= 300:
            raise GoogleError(f"HTTP {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}


def _cell(v: Any) -> Any:
    return "" if v is None else v


def _truthy(v: Any) -> int:
    return 0 if str(v).strip().lower() in ("false", "0", "no", "n", "") else 1
