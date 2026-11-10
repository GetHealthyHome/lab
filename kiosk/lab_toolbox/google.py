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

from .db import now_iso, tx
from .sync import pending

SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE_UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
# A person's sign-in is only asked for the files this app makes: all it needs, and all Google's device sign-in allows.
DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"

# Outbox kind -> Sheet tab. "photo" goes to Drive instead.
TABS = {"transaction": "Transactions", "snapshot": "Snapshots", "alert": "Alerts", "audit": "AuditLog",
        "reference": "References", "person": "People", "tool": "Tools", "kit": "Kits", "damage": "DamageReports",
        "reprint": "ReprintQueue", "extension": "Extensions"}
HEALTH_TAB = "Health"
# Payload fields that hold a local photo path; swapped for the Drive link before the row is written.
PHOTO_FIELDS = ("gate_photo", "photo", "snapshot_photo", "reference_photo")
# Kinds whose rows are updated in place (by this key) instead of appended again (C6).
# person/tool/kit payloads carry only the cells the kiosk changed (a PIN, a new badge, quarantine).
UPSERT_KEYS = {"alert": "alert_id", "person": "person_id", "tool": "asset_id", "kit": "kit_id", "damage": "report_id"}
# Columns the dashboard owns, per kind: an update from the kiosk never overwrites them.
DASHBOARD_FIELDS = {"alert": {"status", "resolved_by", "resolution_note", "emailed_at"}}
TRUTHY_TOOL_FIELDS = ("needs_battery", "is_battery")
ALERT_STATUSES = ("open", "acknowledged", "resolved", "false_alarm")


class GoogleError(Exception):
    pass


class GoogleSink:
    """Implements sync.Sink. Rows go under the tab's header row, matched by column name, so staff can reorder columns."""

    def __init__(self, session, conn: sqlite3.Connection, sheet_id: str, folder_id: str, drive=None):
        self.http, self.conn, self.sheet_id, self.folder_id = session, conn, sheet_id, folder_id
        self.drive = drive or session  # photo uploads can use a different sign-in from the Sheet's
        self._headers: dict[str, list[str]] = {}

    @classmethod
    def from_service_account(cls, key_file: str, conn: sqlite3.Connection, sheet_id: str, folder_id: str,
                             user_file: str | None = None) -> "GoogleSink":
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(key_file, scopes=SCOPES)
        drive = None
        if user_file and os.path.exists(user_file):
            # A service account has no Drive storage, so outside a Shared Drive its uploads are refused.
            # drive_login leaves a person's sign-in here: photos go up as them, into the folder it made.
            from google.oauth2.credentials import Credentials

            with open(user_file) as f:
                info = json.load(f)
            drive = AuthorizedSession(Credentials.from_authorized_user_info(info, [DRIVE_FILE_SCOPE]))
            folder_id = info["folder_id"]
        return cls(AuthorizedSession(creds), conn, sheet_id, folder_id, drive)

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
        self._append(TABS[kind], row, UPSERT_KEYS.get(kind), DASHBOARD_FIELDS.get(kind, set()))

    def report_health(self, kiosk_id: str) -> None:
        """D8: one Health row per kiosk, rewritten after every push, so the dashboard sees when it last heard from us."""
        last = self.conn.execute("SELECT MAX(ts) FROM snapshots").fetchone()[0]
        self._append(HEALTH_TAB, {"kiosk_id": kiosk_id, "last_sync": now_iso(), "queue_size": pending(self.conn),
                                  "last_snapshot": last}, "kiosk_id")

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
            r = self.drive.post(
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

    def _append(self, tab: str, row: dict[str, Any], key: str | None = None, keep: set[str] = frozenset()) -> None:
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
                    # Only our own cells are written, so a dashboard edit made a moment ago is never put back.
                    data = [{"range": f"{tab}!{_col(c)}{i}", "values": [[_cell(row[col])]]}
                            for c, col in enumerate(header) if col in row and col not in keep]
                    r = self.http.post(f"{SHEETS}/{self.sheet_id}/values:batchUpdate",
                                       json={"valueInputOption": "RAW", "data": data})
                    self._ok(r)
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
            try:
                rows = self._get(f"{tab}!1:1")
            except GoogleError as e:
                if "Unable to parse range" not in str(e):
                    raise
                # The tab isn't in the Sheet: add it, so one missed during setup doesn't hold up the whole queue
                self._ok(self.http.post(f"{SHEETS}/{self.sheet_id}:batchUpdate",
                                        json={"requests": [{"addSheet": {"properties": {"title": tab}}}]}))
                rows = []
            self._headers[tab] = [str(c) for c in rows[0]] if rows else []
        return self._headers[tab]

    # -- pull ---------------------------------------------------------------

    def pull(self) -> dict[str, int]:
        """Copy Spots, Tools, People and Settings down from the Sheet (supervisors edit them there), plus the
        dashboard's answer on each alert. Returns rows per tab."""
        spots, tools, people = self._records("Spots", "spot_id"), self._records("Tools", "asset_id"), self._records("People", "person_id")
        settings, alerts = self._records("Settings", "key"), self._records("Alerts", "alert_id")
        # D7 corrections staff made on the dashboard, like "record it as returned"
        fixes = [t for t in self._records("Transactions", "txn_id", optional=True) or [] if t.get("kiosk_id") == "dashboard"]
        # Batteries and kits (K12, K13, D13): optional tabs, so a Sheet set up before them still syncs.
        groups = self._records("BatteryGroups", "group_id", optional=True) or []
        kits = self._records("Kits", "kit_id", optional=True) or []
        kit_items = self._records("KitItems", "kit_id", optional=True)
        # The calibration page stores the reference photo's Drive link; the Pi compares against its own copy.
        local = {r["link"]: r["path"] for r in self.conn.execute("SELECT link, path FROM drive_photos")}
        with tx(self.conn):  # all three or none, so a half-read Sheet never leaves tools pointing at missing spots
            for s in spots:
                self.conn.execute(
                    "INSERT INTO spots (spot_id, label, box, reference_photo) VALUES (:spot_id, :label, :box, :reference_photo)"
                    " ON CONFLICT(spot_id) DO UPDATE SET label=excluded.label, box=excluded.box,"
                    " reference_photo=excluded.reference_photo",
                    {"spot_id": s["spot_id"], "label": s.get("label") or s["spot_id"],
                     "box": s.get("box") or None,
                     "reference_photo": local.get(s.get("reference_photo"), s.get("reference_photo")) or None},
                )
            for t in tools:
                # Sheet status may say out/missing; locally those come from transactions and alerts, not this column.
                status = t.get("status") if t.get("status") in ("quarantined", "retired") else "available"
                cols = ("qr_code", "name", "category", "spot_id", "status", "notes", "serial", "brand", "model",
                        "needs_battery", "battery_group_id", "is_battery")
                row = {c: (t.get(c) or None) for c in cols}
                row.update(asset_id=t["asset_id"], qr_code=t["qr_code"], name=t["name"], status=status,
                           **{c: _truthy(t.get(c, "")) for c in TRUTHY_TOOL_FIELDS})
                self.conn.execute(
                    f"INSERT INTO tools (asset_id, {', '.join(cols)}) VALUES (:asset_id, {', '.join(':' + c for c in cols)})"
                    f" ON CONFLICT(asset_id) DO UPDATE SET {', '.join(f'{c}=excluded.{c}' for c in cols)}", row)
                if t.get("photo"):  # Drive link back to the Pi's own copy; a blank cell never wipes a photo taken here
                    self.conn.execute("UPDATE tools SET photo = ? WHERE asset_id = ?", (local.get(t["photo"], t["photo"]), t["asset_id"]))
            for p in people:
                self.conn.execute(
                    "INSERT INTO people (person_id, id_code, name, role, pin_hash, active, email)"
                    " VALUES (:person_id, :id_code, :name, :role, :pin_hash, :active, :email)"
                    " ON CONFLICT(person_id) DO UPDATE SET id_code=excluded.id_code, name=excluded.name,"
                    " role=excluded.role, pin_hash=excluded.pin_hash, active=excluded.active, email=excluded.email",
                    {"person_id": p["person_id"], "id_code": p["id_code"], "name": p["name"], "email": p.get("email") or None,
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
            for g in groups:
                self.conn.execute("INSERT INTO battery_groups VALUES (?, ?) ON CONFLICT(group_id) DO UPDATE SET name=excluded.name",
                                  (str(g["group_id"]), str(g.get("name") or g["group_id"])))
            for k in kits:
                self.conn.execute(
                    "INSERT INTO kits (kit_id, case_code, name, spot_id, check_mode, status)"
                    " VALUES (:kit_id, :case_code, :name, :spot_id, :check_mode, :status) ON CONFLICT(kit_id) DO UPDATE SET"
                    " case_code=excluded.case_code, name=excluded.name, spot_id=excluded.spot_id,"
                    " check_mode=excluded.check_mode, status=excluded.status",
                    {"kit_id": k["kit_id"], "case_code": k["case_code"], "name": k.get("name") or k["kit_id"],
                     "spot_id": k.get("spot_id") or None,
                     "check_mode": "verified" if str(k.get("check_mode")).lower() == "verified" else "standard",
                     "status": "incomplete" if str(k.get("status")).lower() == "incomplete" else "available"})
            if kit_items is not None:  # the dashboard can remove pieces, so the tab replaces the local list
                self.conn.execute("DELETE FROM kit_items")
                for ki in kit_items:  # a row naming an unknown kit or tool is skipped, not fatal
                    self.conn.execute("INSERT OR IGNORE INTO kit_items SELECT :kit_id, :asset_id"
                                      " WHERE EXISTS (SELECT 1 FROM kits WHERE kit_id = :kit_id)"
                                      " AND EXISTS (SELECT 1 FROM tools WHERE asset_id = :asset_id)",
                                      {"kit_id": ki["kit_id"], "asset_id": ki.get("asset_id")})
            for t in fixes:
                self.conn.execute(
                    "INSERT OR IGNORE INTO transactions (txn_id, ts, asset_id, person_id, action, session_id, gate_photo,"
                    " override_by, kiosk_id) SELECT :txn_id, :ts, :asset_id, :person_id, :action, :session_id,"
                    " NULL, (SELECT person_id FROM people WHERE person_id = :override_by), 'dashboard'"
                    " WHERE EXISTS (SELECT 1 FROM tools WHERE asset_id = :asset_id)"
                    " AND EXISTS (SELECT 1 FROM people WHERE person_id = :person_id) AND :action IN ('out', 'in')",
                    {k: t.get(k) or None for k in ("txn_id", "ts", "asset_id", "person_id", "action", "session_id", "override_by")})
        return {"Spots": len(spots), "Tools": len(tools), "People": len(people), "Settings": len(settings)}

    def _records(self, tab: str, key: str, optional: bool = False) -> list[dict[str, Any]] | None:
        try:
            rows = self._get(tab)
        except GoogleError:
            if not optional:
                raise
            return None  # a tab that doesn't exist yet: nothing to copy, and nothing to remove
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


def _col(i: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(v: Any) -> Any:
    return "" if v is None else v


def _truthy(v: Any) -> int:
    return 0 if str(v).strip().lower() in ("false", "0", "no", "n", "") else 1
