"""Cage monitor (C1, C5-C7): its own process, like a guard who walks past the shelves on a timer.

A snapshot is taken:
- every `snapshot_interval_hours` hours (Settings tab, synced down from the Sheet; default 1),
- a couple of minutes after each return session, once the tools have had time to go back on their hooks,
- when the dashboard changes the `snapshot_request` setting (any new value means "take one now"),
- one minute after any snapshot that saw a problem, so a problem is only reported once two snapshots
  in a row agree (C7): a hand reaching in for a tool never raises an alarm.

Run: KIOSK_DB=kiosk.db PHOTO_DIR=photos CAGE_CAMERA=/dev/video0 python -m lab_toolbox.cage
"""
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw

from . import db
from .db import tx
from .spots import classify, decide, load_gray
from .sync import enqueue

log = logging.getLogger("lab_toolbox.cage")

DEFAULT_INTERVAL_HOURS = 1.0
RETURN_DELAY = timedelta(minutes=2)
CONFIRM_DELAY = timedelta(minutes=1)
POLL_SECONDS = 15
OPEN = ("open", "acknowledged")


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def setting(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def interval(conn) -> timedelta:
    raw = setting(conn, "snapshot_interval_hours")
    try:
        hours = float(raw) if raw not in (None, "") else DEFAULT_INTERVAL_HOURS
    except ValueError:
        log.warning("snapshot_interval_hours %r is not a number; using %s", raw, DEFAULT_INTERVAL_HOURS)
        hours = DEFAULT_INTERVAL_HOURS
    return timedelta(hours=hours if hours > 0 else DEFAULT_INTERVAL_HOURS)


class Monitor:
    def __init__(self, conn, camera, photo_dir: str):
        """camera: anything with snapshot(path) -> path (see hardware.UsbCageCamera)."""
        self.conn, self.camera = conn, camera
        self.photo_dir = Path(photo_dir)
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        self.confirm_at: datetime | None = None
        # A request already in the Sheet when we start was answered before the restart (or never will be).
        self.seen_request = setting(conn, "snapshot_request")

    def due(self, now: datetime) -> str | None:
        """Why a snapshot should be taken now, or None."""
        if self.confirm_at and now >= self.confirm_at:
            return "confirm"
        request = setting(self.conn, "snapshot_request")
        if request != self.seen_request:
            return "on_demand"
        last = self.conn.execute("SELECT MAX(ts) FROM snapshots").fetchone()[0]
        ret = self.conn.execute("SELECT MAX(ts) FROM transactions WHERE action = 'in'").fetchone()[0]
        if ret and (not last or ret > last) and now - _ts(ret) >= RETURN_DELAY:
            return "after_return"
        if not last or now - _ts(last) >= interval(self.conn):
            return "scheduled"
        return None

    def take(self, now: datetime, reason: str) -> dict:
        """One snapshot: classify every calibrated spot, compare with the records, open or update alerts."""
        self.seen_request = setting(self.conn, "snapshot_request")
        self.confirm_at = None
        snap_id = uuid.uuid4().hex
        try:
            photo = self.camera.snapshot(str(self.photo_dir / f"cage-{now:%Y%m%d-%H%M%S}-{snap_id[:6]}.jpg"))
            current = load_gray(photo)
        except Exception:  # unplugged or blocked camera: every spot is "unclear", which becomes a Check camera alert
            log.exception("cage snapshot failed")
            photo, current = None, None

        prev_row = self.conn.execute("SELECT spots FROM snapshots ORDER BY ts DESC, rowid DESC LIMIT 1").fetchone()
        prev = json.loads(prev_row["spots"]) if prev_row else {}
        seen, problems, refs = {}, [], {}
        for s in self._spots():
            if current is None:
                seen[s["spot_id"]] = "unclear"
            else:
                ref = s["reference_photo"]
                if ref not in refs:
                    refs[ref] = load_gray(ref) if os.path.exists(ref) else None
                if refs[ref] is None:
                    log.warning("spot %s: reference photo %s is not on this Pi; skipped", s["spot_id"], ref)
                    continue
                seen[s["spot_id"]] = classify(refs[ref], current, s["box"])
            checked_out = s["state"] != "available"  # out, quarantined or retired: the hook should be empty
            kind = decide(seen[s["spot_id"]], checked_out)
            if not kind:
                continue
            if s["spot_id"] in prev and decide(prev[s["spot_id"]], checked_out) == kind:
                problems.append((s, kind))
            else:
                self.confirm_at = now + CONFIRM_DELAY  # first sighting: look again before alerting (C7)

        ts = _iso(now)
        highlights = {s["spot_id"]: self._highlight(photo, s) for s, _ in problems} if photo else {}
        counts = {k: sum(v == k for v in seen.values()) for k in ("full", "empty", "unclear")}
        alerts = []
        with tx(self.conn):
            self.conn.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?)", (snap_id, ts, photo, json.dumps(seen)))
            if photo:
                enqueue(self.conn, "photo", {"path": photo, "snapshot_id": snap_id, "ts": ts})
            enqueue(self.conn, "snapshot", {"snapshot_id": snap_id, "ts": ts, "reason": reason, "photo": photo,
                                            **counts, "spots": json.dumps(seen)})
            for s, kind in problems:
                pic = highlights.get(s["spot_id"])
                if pic:
                    enqueue(self.conn, "photo", {"path": pic, "snapshot_id": snap_id, "ts": ts})
                alerts.append(self._alert(s, kind, ts, pic))
        return {"snapshot_id": snap_id, "reason": reason, "spots": seen, "alerts": alerts}

    def _spots(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT s.spot_id, s.label, s.box, s.reference_photo, t.asset_id, t.name, t.state"
            " FROM spots s JOIN tool_state t ON t.spot_id = s.spot_id"
            " WHERE s.box IS NOT NULL AND s.reference_photo IS NOT NULL ORDER BY s.spot_id"
        ).fetchall()
        out = []
        for r in rows:
            try:
                box = tuple(int(v) for v in json.loads(r["box"]))
                assert len(box) == 4
            except Exception:
                log.warning("spot %s: box %r should look like [x, y, w, h]; skipped", r["spot_id"], r["box"])
                continue
            out.append({**dict(r), "box": box})
        return out

    def _highlight(self, photo: str, s: dict) -> str:
        """C5: a copy of the snapshot with the spot boxed in red."""
        x, y, w, h = s["box"]
        img = Image.open(photo).convert("RGB")
        ImageDraw.Draw(img).rectangle((x, y, x + w, y + h), outline=(255, 0, 0), width=max(3, img.width // 200))
        path = str(Path(photo).with_name(f"{Path(photo).stem}-{s['spot_id']}.jpg"))
        img.save(path, quality=85)
        return path

    def _alert(self, s: dict, kind: str, ts: str, pic: str | None) -> dict:
        """C5 fields; C6: an open alert for the same spot is updated, not repeated."""
        last = self.conn.execute(
            "SELECT person_id, ts, gate_photo FROM transactions WHERE asset_id = ? AND action = 'out'"
            " ORDER BY rowid DESC LIMIT 1", (s["asset_id"],)
        ).fetchone()
        fields = {"type": kind, "asset_id": s["asset_id"], "last_person_id": last and last["person_id"],
                  "last_checkout": last and last["ts"], "snapshot_photo": pic, "gate_photo": last and last["gate_photo"],
                  "last_seen": ts}
        row = self.conn.execute(
            f"SELECT alert_id, opened FROM alerts WHERE spot_id = ? AND status IN {OPEN}", (s["spot_id"],)
        ).fetchone()
        if row:
            alert = {"alert_id": row["alert_id"], "opened": row["opened"], **fields}
            self.conn.execute(
                "UPDATE alerts SET type = :type, asset_id = :asset_id, last_person_id = :last_person_id,"
                " last_checkout = :last_checkout, snapshot_photo = :snapshot_photo, gate_photo = :gate_photo,"
                " last_seen = :last_seen WHERE alert_id = :alert_id", alert)
        else:
            alert = {"alert_id": uuid.uuid4().hex, "opened": ts, **fields}
            self.conn.execute(
                "INSERT INTO alerts (alert_id, opened, type, spot_id, asset_id, last_person_id, last_checkout,"
                " snapshot_photo, gate_photo, last_seen) VALUES (:alert_id, :opened, :type, :spot_id, :asset_id,"
                " :last_person_id, :last_checkout, :snapshot_photo, :gate_photo, :last_seen)",
                {**alert, "spot_id": s["spot_id"]})
        person = alert["last_person_id"] and self.conn.execute(
            "SELECT name FROM people WHERE person_id = ?", (alert["last_person_id"],)).fetchone()
        # The Sheet row also gets readable names, so the dashboard needs no lookups (C5).
        payload = {**alert, "spot_id": s["spot_id"], "spot_label": s["label"], "tool_name": s["name"],
                   "last_person_name": person["name"] if person else None, "status": "open"}
        enqueue(self.conn, "alert", payload)
        return payload


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from .hardware import UsbCageCamera

    conn = db.connect(os.environ.get("KIOSK_DB", "kiosk.db"))
    m = Monitor(conn, UsbCageCamera(os.environ.get("CAGE_CAMERA", "/dev/video0")), os.environ.get("PHOTO_DIR", "photos"))
    while True:
        now = datetime.now(timezone.utc)
        try:
            if reason := m.due(now):
                r = m.take(now, reason)
                log.info("snapshot (%s): %s; %d alert(s)", reason, r["spots"], len(r["alerts"]))
        except Exception:  # a bad round (DB busy, odd image) must not stop the monitor for the night
            log.exception("cage round failed")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
