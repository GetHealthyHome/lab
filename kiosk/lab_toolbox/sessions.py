"""Checkout and return sessions at the gate (K2-K6, K9).

One kiosk, one screen, so at most one session is open at a time. A session is like a shopping basket:
scans add to it, Done saves it as one loan record per tool, and walking away saves it after 45 seconds.
"""
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .db import now_iso, tx
from .sync import enqueue

log = logging.getLogger(__name__)

IDLE_TIMEOUT_S = 45  # K9


class Rejected(Exception):
    """A scan or request the kiosk refuses; `reason` is shown on screen as-is."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class GateCamera(Protocol):
    def capture(self) -> str:
        """Take a still, save it (about 1280 px wide), return the file path."""
        ...


@dataclass
class Session:
    id: str
    action: str  # "out" | "in"
    person: dict
    items: list[dict] = field(default_factory=list)
    last_activity: float = 0.0


class Kiosk:
    def __init__(
        self,
        conn: sqlite3.Connection,
        kiosk_id: str,
        camera: GateCamera | None = None,
        clock: Callable[[], float] = time.monotonic,
        idle_timeout: float = IDLE_TIMEOUT_S,
    ):
        self.conn = conn
        self.kiosk_id = kiosk_id
        self.camera = camera
        self.clock = clock
        self.idle_timeout = idle_timeout
        self.session: Session | None = None
        self.next_action = "out"  # the screen's Check out / Return choice for the next ID scan

    # -- session lifecycle -------------------------------------------------

    def start(self, id_code: str, action: str) -> Session:
        if action not in ("out", "in"):
            raise Rejected("Choose Check out or Return")
        if self.session:
            raise Rejected("Finish the current session first")
        person = self.conn.execute(
            "SELECT person_id, name, role FROM people WHERE id_code = ? AND active = 1", (id_code.strip(),)
        ).fetchone()
        if not person:
            raise Rejected("ID not recognized. Ask an instructor for help.")
        self.session = Session(uuid.uuid4().hex, action, dict(person), last_activity=self.clock())
        return self.session

    def handle(self, code: str) -> dict:
        """One code from the gate camera: with no open session it's an ID, otherwise a tool."""
        if self.session:
            return self.scan(code)
        s = self.start(code, self.next_action)
        self.next_action = "out"
        return {"id": s.id, "action": s.action, "person": s.person["name"]}

    def scan(self, code: str) -> dict:
        s = self._current()
        tool = self.conn.execute("SELECT * FROM tool_state WHERE qr_code = ?", (code.strip(),)).fetchone()
        if not tool:
            if self.conn.execute("SELECT 1 FROM people WHERE id_code = ?", (code.strip(),)).fetchone():
                raise Rejected("That's an ID card. Scan a tool label.")
            raise Rejected("Unknown code. Ask an instructor for help.")
        if any(i["asset_id"] == tool["asset_id"] for i in s.items):
            raise Rejected(f"{tool['name']} is already in your list")
        if tool["state"] == "retired":
            raise Rejected(f"{tool['name']} is retired and can't be used")
        if tool["state"] == "quarantined":
            raise Rejected(f"{tool['name']} is waiting for an instructor to inspect it")
        if s.action == "out" and tool["state"] == "out":
            if tool["holder_id"] == s.person["person_id"]:
                raise Rejected(f"You already have {tool['name']} checked out")
            raise Rejected(f"{tool['name']} is checked out by someone else")
        if s.action == "in" and tool["state"] != "out":
            raise Rejected(f"{tool['name']} isn't checked out")

        item = {"asset_id": tool["asset_id"], "name": tool["name"], "spot_id": tool["spot_id"]}
        s.items.append(item)
        s.last_activity = self.clock()  # each scan resets the countdown
        return item

    def finish(self) -> dict:
        """Save the session: one transaction per tool, one gate photo shared by all of them (K3, K4)."""
        s = self._current()
        self.session = None
        if not s.items:
            return {"session_id": s.id, "action": s.action, "items": []}

        photo = self._take_photo()
        ts = now_iso()
        with tx(self.conn):
            if photo:
                enqueue(self.conn, "photo", {"path": photo, "session_id": s.id, "ts": ts})
            for item in s.items:
                txn = {
                    "txn_id": uuid.uuid4().hex,
                    "ts": ts,
                    "asset_id": item["asset_id"],
                    "person_id": s.person["person_id"],
                    "action": s.action,
                    "session_id": s.id,
                    "gate_photo": photo,
                    "override_by": None,
                    "kiosk_id": self.kiosk_id,
                }
                self.conn.execute(
                    "INSERT INTO transactions VALUES (:txn_id, :ts, :asset_id, :person_id, :action,"
                    " :session_id, :gate_photo, :override_by, :kiosk_id)",
                    txn,
                )
                enqueue(self.conn, "transaction", txn)

        items = [dict(i) for i in s.items]
        if s.action == "in":  # K5: tell them where each tool goes back
            for i in items:
                spot = i["spot_id"] and self.conn.execute(
                    "SELECT label FROM spots WHERE spot_id = ?", (i["spot_id"],)
                ).fetchone()
                i["return_to"] = spot["label"] if spot else None
        return {"session_id": s.id, "action": s.action, "person": s.person["name"], "items": items, "photo": photo}

    def cancel(self) -> None:
        self.session = None

    # -- idle timeout (K9) -------------------------------------------------

    def seconds_left(self) -> float | None:
        if not self.session:
            return None
        return max(0.0, self.idle_timeout - (self.clock() - self.session.last_activity))

    def expire_idle(self) -> dict | None:
        """Call about once a second. A walked-away session is saved, not dropped: the tools left with them."""
        left = self.seconds_left()
        if left is None or left > 0:
            return None
        return self.finish()

    # -- helpers ------------------------------------------------------------

    def _current(self) -> Session:
        if not self.session:
            raise Rejected("Scan your ID to start")
        return self.session

    def _take_photo(self) -> str | None:
        if not self.camera:
            return None
        try:
            return self.camera.capture()
        except Exception:  # a camera fault must never block a checkout
            log.exception("gate photo failed")
            return None
