"""Checkout and return sessions at the gate (K2-K9, K11-K17).

One kiosk, one screen, so at most one session is open at a time. A session is like a shopping basket:
people log in with their PIN (4 digits for students, 6 for staff), tool stickers are tapped on the NFC reader
(or a printed QR code shown to the camera), each tool tapped out gets a photo of the person, Done saves one loan
record per tool, and walking away saves it after 45 seconds. An instructor can step in at any point (K7): tap
Instructor, enter their PIN, and the kiosk takes instructor actions until they log out or walk away.
"""
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Protocol

from . import loans, pins
from .db import now_iso, tx
from .sync import enqueue

log = logging.getLogger(__name__)

IDLE_TIMEOUT_S = 45  # K9
SEVERITIES = ("minor", "needs_inspection", "safety_hazard")  # K14
LABEL_CONDITIONS = ("Faded", "Scratched", "Peeling", "Torn")  # K15
STAFF = ("instructor", "supervisor")


class Rejected(Exception):
    """A scan or request the kiosk refuses; `reason` is shown on screen as-is, `sound` is what the buzzer plays."""

    sound = "rejected"

    def __init__(self, reason: str, **details):
        super().__init__(reason)
        self.reason = reason
        self.details = details


class WrongBattery(Rejected):
    """K12: amber border, required and scanned battery side by side, falling two-tone."""

    sound = "wrong_battery"


class Locked(Rejected):
    """K11: red lock screen with a countdown (`details["until"]`), warble alarm."""

    sound = "locked"


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
    override_by: dict | None = None  # instructor who started it for a student whose ID won't scan (K7)
    kits: dict = field(default_factory=dict)  # kit_id -> {name, mode, expect, got, confirmed} (K13)


class Kiosk:
    def __init__(
        self,
        conn: sqlite3.Connection,
        kiosk_id: str,
        camera: GateCamera | None = None,
        clock: Callable[[], float] = time.monotonic,
        idle_timeout: float = IDLE_TIMEOUT_S,
        wall: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self.conn = conn
        self.kiosk_id = kiosk_id
        self.camera = camera
        self.clock = clock
        self.wall = wall
        self.idle_timeout = idle_timeout
        self.session: Session | None = None
        self.next_action = "out"  # the screen's Check out / Return / Instructor choice for the next PIN
        self.instructor: dict | None = None  # logged-in instructor (K7)
        self.instructor_seen = 0.0
        self.link_for: dict | None = None  # the next NFC sticker tapped becomes this tool's
        self.pin_fails = 0  # wrong PINs in a row, any person: the PIN alone says who you are
        self.pad_locked_until: datetime | None = None

    # -- session lifecycle -------------------------------------------------

    def start(self, id_code: str | None, action: str, person_id: str | None = None, override_by: dict | None = None) -> Session:
        if action not in ("out", "in"):
            raise Rejected("Choose Check out or Return")
        if self.session:
            raise Rejected("Finish the current session first")
        if person_id:
            person = self.conn.execute("SELECT * FROM people WHERE person_id = ? AND active = 1", (person_id,)).fetchone()
        else:
            person = self.conn.execute("SELECT * FROM people WHERE id_code = ? AND active = 1", ((id_code or "").strip(),)).fetchone()
        if not person:
            raise Rejected("ID not recognized. Ask an instructor for help.")
        if action == "out" and not override_by:
            held = loans.held_for_overdue(self.conn, person["person_id"], self.wall())
            if held:
                raise Rejected("Return your overdue tools first: " + ", ".join(h["name"] for h in held)
                               + ". Ask an instructor if you need more.", overdue=held)
        p = {k: person[k] for k in ("person_id", "name", "role")}
        self.session = Session(uuid.uuid4().hex, action, p, last_activity=self.clock(), override_by=override_by)
        return self.session

    def handle(self, code: str) -> dict:
        """One tool code: an NFC sticker's UID, or a printed label read by the gate camera (the fallback)."""
        code = code.strip()
        if self.link_for and self.instructor:
            return self.link_tag(self.link_for["asset_id"], code)
        if self.session:
            return self.scan(code)
        raise Rejected("Enter your PIN to start")

    def _started(self, s: Session) -> dict:  # also used by POST /api/instructor/start
        return {"id": s.id, "action": s.action, "person": s.person["name"]}

    # -- PIN login ----------------------------------------------------------

    def login(self, pin: str) -> dict:
        """The PIN pad: starts the chosen session, or logs an instructor in (Instructor button, any screen)."""
        if self.pad_locked_until and self.wall() < self.pad_locked_until:
            raise Locked("Too many wrong PINs. Wait a minute and try again.", until=self.pad_locked_until.isoformat(timespec="seconds"))
        if not pins.valid(pin):
            raise Rejected("Students: 4 digits. Instructors: 6 digits.")
        person = pins.find(self.conn, pin)
        if not person:
            self._wrong_pin()
        self.pin_fails = 0
        if self.next_action == "instructor":
            if person["role"] not in STAFF:
                raise Rejected("Instructors only")
            self.next_action = "out"
            self.instructor = {k: person[k] for k in ("person_id", "name", "role")}
            self.instructor_seen = self.clock()
            return {"instructor": self.instructor["name"]}
        return self._started(self.start(None, self.next_action, person_id=person["person_id"]))

    def _wrong_pin(self):
        self.pin_fails += 1
        if self.pin_fails < pins.MAX_TRIES:
            raise Rejected("PIN not recognized. Try again, or ask an instructor.")
        self.pin_fails = 0
        self.pad_locked_until = self.wall() + pins.LOCK_FOR
        until = self.pad_locked_until.isoformat(timespec="seconds")
        with tx(self.conn):
            self._open_alert("id_locked", details=f"{pins.MAX_TRIES} wrong PINs in a row at the kiosk; keypad locked until {until}")
        raise Locked("Too many wrong PINs. Wait a minute and try again.", until=until)

    # -- scanning tools -----------------------------------------------------

    def scan(self, code: str, by_asset: bool = False, override_by: dict | None = None) -> dict:
        s = self._current()
        where = "asset_id = ?" if by_asset else "(qr_code = ? OR nfc_uid = ?1)"
        tool = self.conn.execute(f"SELECT * FROM tool_state WHERE {where}", (code.strip(),)).fetchone()
        if not tool:
            kit = None if by_asset else self.conn.execute("SELECT * FROM kits WHERE case_code = ?", (code.strip(),)).fetchone()
            if kit:
                return self._scan_kit(s, kit)
            raise Rejected("Unknown code. Ask an instructor for help." if not by_asset else f"No tool has asset number {code}")
        if any(i["asset_id"] == tool["asset_id"] for i in s.items):
            raise Rejected(f"{tool['name']} is already in your list")
        self._check_tool(s, tool)

        kit_id = self._kit_for(s, tool["asset_id"])
        item = {"asset_id": tool["asset_id"], "name": tool["name"], "spot_id": tool["spot_id"]}
        if kit_id:
            item["kit_id"] = kit_id
        if override_by:
            item["override_by"] = override_by["person_id"]
        result = dict(item)
        if s.action == "out" and tool["is_battery"]:
            waiting = next((i for i in s.items if i.get("needs_battery") and not i.get("battery")), None)
            if waiting:
                if waiting["needs_battery"] != tool["battery_group_id"]:
                    need = self._group_name(waiting["needs_battery"])
                    raise WrongBattery(f"{waiting['name']} needs a {need} battery. Scan a {need} battery.",
                                       required=need, scanned=self._group_name(tool["battery_group_id"]))
                waiting["battery"] = tool["asset_id"]
                item["paired_to"] = waiting["asset_id"]
                result["paired_to"] = waiting["name"]
        if s.action == "out" and tool["needs_battery"] and not self._is_kit_with_battery(s, tool):
            item["needs_battery"] = tool["battery_group_id"]
            item["battery"] = None
            result["battery_needed"] = self._battery_options(tool["battery_group_id"])
        if kit_id:
            s.kits[kit_id]["got"].add(tool["asset_id"])
        if s.action == "out":
            item["photo"] = self._take_photo()  # who took it, the moment it's tapped (Done fills any gaps)
        s.items.append(item)
        s.last_activity = self.clock()  # each scan resets the countdown
        return result

    def _check_tool(self, s: Session, tool) -> None:
        if s.action == "in":
            # A return only needs the tool to be out: a tool quarantined while out (damage reported) still comes back.
            if tool["last_action"] != "out":
                raise Rejected(f"{tool['name']} isn't checked out")
            return
        if tool["state"] == "retired":
            raise Rejected(f"{tool['name']} is retired and can't be used")
        if tool["state"] == "quarantined":
            raise Rejected(f"{tool['name']} is waiting for an instructor to inspect it")
        if tool["state"] == "out":
            if tool["holder_id"] == s.person["person_id"]:
                raise Rejected(f"You already have {tool['name']} checked out")
            raise Rejected(f"{tool['name']} is checked out by someone else")

    def remove(self, asset_id: str) -> dict:
        """Take a tool back out of the basket (for example a drill when no battery fits)."""
        s = self._current()
        item = next((i for i in s.items if i["asset_id"] == asset_id), None)
        if not item:
            raise Rejected("That tool isn't in your list")
        s.items.remove(item)
        for i in s.items:
            if i.get("battery") == asset_id:
                i["battery"] = None
            if i.get("paired_to") == asset_id:
                i.pop("paired_to")
        if item.get("kit_id") in s.kits:
            s.kits[item["kit_id"]]["got"].discard(asset_id)
        s.last_activity = self.clock()
        return {"removed": asset_id}

    # -- batteries (K12) ------------------------------------------------------

    def _group_name(self, group_id: str | None) -> str:
        row = group_id and self.conn.execute("SELECT name FROM battery_groups WHERE group_id = ?", (group_id,)).fetchone()
        return row["name"] if row else (group_id or "matching")

    def _battery_options(self, group_id: str | None) -> dict:
        """Which batteries fit, and where the charged ones sit."""
        rows = self.conn.execute(
            "SELECT t.asset_id, t.name, s.label AS spot FROM tool_state t LEFT JOIN spots s ON s.spot_id = t.spot_id"
            " WHERE t.is_battery = 1 AND t.battery_group_id IS ? AND t.state = 'available' ORDER BY t.spot_id, t.asset_id",
            (group_id,)).fetchall()
        return {"group": self._group_name(group_id), "available": [dict(r) for r in rows]}

    def _is_kit_with_battery(self, s: Session, tool) -> bool:
        """A kit that carries its own battery counts as paired: the case is checked out as one."""
        kit_id = self._kit_for(s, tool["asset_id"])
        if not kit_id:
            return False
        return bool(self.conn.execute(
            "SELECT 1 FROM kit_items k JOIN tools t ON t.asset_id = k.asset_id"
            " WHERE k.kit_id = ? AND t.is_battery = 1 AND t.battery_group_id IS ?", (kit_id, tool["battery_group_id"])).fetchone())

    # -- kits (K13) -------------------------------------------------------------

    def _kit_for(self, s: Session, asset_id: str) -> str | None:
        return next((k for k, v in s.kits.items() if asset_id in v["expect"]), None)

    def _scan_kit(self, s: Session, kit) -> dict:
        if kit["kit_id"] in s.kits:
            raise Rejected(f"{kit['name']} is already in your list")
        pieces = self.conn.execute(
            "SELECT t.* FROM kit_items k JOIN tool_state t ON t.asset_id = k.asset_id WHERE k.kit_id = ? ORDER BY t.asset_id",
            (kit["kit_id"],)).fetchall()
        if s.action == "out":
            ready = [p for p in pieces if p["state"] == "available"]
            skipped = [p["name"] for p in pieces if p["state"] != "available"]
        else:
            ready = [p for p in pieces if p["last_action"] == "out"]
            skipped = []
        if not ready:
            raise Rejected(f"None of {kit['name']} is {'available' if s.action == 'out' else 'checked out'}")
        s.kits[kit["kit_id"]] = {"name": kit["name"], "mode": kit["check_mode"], "expect": {p["asset_id"] for p in ready},
                                 "got": set(), "confirmed": s.action == "out"}
        s.last_activity = self.clock()
        if s.action == "out" and kit["check_mode"] == "standard":
            photo = self._take_photo()
            for p in ready:  # standard mode: the case covers every piece in it
                self._add_kit_piece(s, kit["kit_id"], p, photo)
        out = {"kit_id": kit["kit_id"], "name": kit["name"], "mode": kit["check_mode"],
               "pieces": [{"asset_id": p["asset_id"], "name": p["name"]} for p in ready]}
        if skipped:
            out["not_in_kit"] = skipped  # already out, quarantined or retired; the kit goes without them
        if s.action == "in":
            out["confirm"] = "Scan each piece, then confirm" if kit["check_mode"] == "verified" else "Confirm every piece is back"
        elif kit["check_mode"] == "verified":
            out["confirm"] = "Scan each piece in the kit"
        return out

    def _add_kit_piece(self, s: Session, kit_id: str, p, photo: str | None = None) -> None:
        item = {"asset_id": p["asset_id"], "name": p["name"], "spot_id": p["spot_id"], "kit_id": kit_id}
        if photo:
            item["photo"] = photo
        if p["needs_battery"] and not self._is_kit_with_battery(s, p):
            item.update(needs_battery=p["battery_group_id"], battery=None)
        s.items.append(item)
        s.kits[kit_id]["got"].add(p["asset_id"])

    def confirm_kit(self, kit_id: str, missing: list[str] | None = None) -> dict:
        """Return: the student confirms the pieces. Standard mode: tick the missing ones. Verified: scanned ones count."""
        s = self._current()
        k = s.kits.get(kit_id)
        if not k or s.action != "in":
            raise Rejected("Scan the kit's case first")
        if k["mode"] == "standard":
            gone = set(missing or []) & k["expect"]
            pieces = self.conn.execute(
                f"SELECT * FROM tool_state WHERE asset_id IN ({','.join('?' * len(k['expect']))})", tuple(k["expect"])).fetchall()
            for p in pieces:
                if p["asset_id"] not in gone and p["asset_id"] not in k["got"]:
                    self._add_kit_piece(s, kit_id, p)
        k["confirmed"] = True
        s.last_activity = self.clock()
        return {"kit_id": kit_id, "missing": sorted(k["expect"] - k["got"])}

    # -- saving -------------------------------------------------------------

    def finish(self, force: bool = False) -> dict:
        """Save the session: one transaction per tool (K3). Checkouts carry the photo taken as each tool was tapped;
        returns, and any tool whose photo failed, share one photo taken now (K4).
        force (idle timeout): save what was scanned even if a battery or a kit check is unfinished."""
        s = self.session
        if not s:
            raise Rejected("Enter your PIN to start")
        if not force:
            waiting = [i["name"] for i in s.items if i.get("needs_battery") and not i.get("battery")]
            if waiting:
                raise Rejected(f"Scan a battery for {', '.join(waiting)}, or remove it from your list")
            for k in s.kits.values():
                if s.action == "out" and k["mode"] == "verified" and k["expect"] - k["got"]:
                    raise Rejected(f"Scan every piece of {k['name']}: {', '.join(self._names(k['expect'] - k['got']))} still to scan")
                if s.action == "in" and not k["confirmed"]:
                    raise Rejected(f"Confirm the pieces of {k['name']} first")
        self.session = None
        if not s.items:
            return {"session_id": s.id, "action": s.action, "items": []}

        photo = self._take_photo() if any(not i.get("photo") for i in s.items) else None
        now = self.wall()
        ts = now.isoformat(timespec="seconds")
        due = loans.due_at(self.conn, now).isoformat(timespec="seconds") if s.action == "out" else None
        batteries = {i["asset_id"]: i["battery"] for i in s.items if i.get("battery")}
        with tx(self.conn):
            for p in dict.fromkeys([photo] + [i.get("photo") for i in s.items]):
                if p:
                    enqueue(self.conn, "photo", {"path": p, "session_id": s.id, "ts": ts})
            for item in s.items:
                txn = {
                    "txn_id": uuid.uuid4().hex, "ts": ts, "asset_id": item["asset_id"], "person_id": s.person["person_id"],
                    "action": s.action, "session_id": s.id, "gate_photo": item.get("photo") or photo,
                    "override_by": item.get("override_by") or (s.override_by or {}).get("person_id"),
                    "kiosk_id": self.kiosk_id, "paired_battery_id": batteries.get(item["asset_id"]),
                    "kit_id": item.get("kit_id"), "due_at": due,
                }
                self.conn.execute(
                    "INSERT INTO transactions (txn_id, ts, asset_id, person_id, action, session_id, gate_photo, override_by,"
                    " kiosk_id, paired_battery_id, kit_id, due_at) VALUES (:txn_id, :ts, :asset_id, :person_id, :action,"
                    " :session_id, :gate_photo, :override_by, :kiosk_id, :paired_battery_id, :kit_id, :due_at)", txn)
                enqueue(self.conn, "transaction", txn)
            short = self._short_kits(s)

        items = [dict(i) for i in s.items]
        if s.action == "in":  # K5: tell them where each tool goes back
            for i in items:
                spot = i["spot_id"] and self.conn.execute("SELECT label FROM spots WHERE spot_id = ?", (i["spot_id"],)).fetchone()
                i["return_to"] = spot["label"] if spot else None
        out = {"session_id": s.id, "action": s.action, "person": s.person["name"], "items": items,
               "photo": photo or next((i["photo"] for i in items if i.get("photo")), None)}
        if due:
            out["due_at"] = due
        if short:
            out["kits_short"] = short
        return out

    def _short_kits(self, s: Session) -> list[dict]:
        """K13: a kit that came back short is marked incomplete and opens an alert naming the missing pieces."""
        short = []
        if s.action != "in":
            return short
        for kit_id, k in s.kits.items():
            gone = k["expect"] - k["got"]
            if not gone:
                continue
            names = self._names(gone)
            self.conn.execute("UPDATE kits SET status = 'incomplete' WHERE kit_id = ?", (kit_id,))
            enqueue(self.conn, "kit", {"kit_id": kit_id, "status": "incomplete"})
            self._open_alert("kit_incomplete", person_id=s.person["person_id"], kit_id=kit_id,
                             details=f"{k['name']} came back without: {', '.join(names)}")
            short.append({"kit_id": kit_id, "name": k["name"], "missing": names})
        return short

    def _names(self, asset_ids) -> list[str]:
        ids = sorted(asset_ids)
        rows = self.conn.execute(f"SELECT asset_id, name FROM tools WHERE asset_id IN ({','.join('?' * len(ids))})", ids).fetchall()
        return [r["name"] for r in sorted(rows, key=lambda r: r["asset_id"])]

    def cancel(self) -> None:
        self.session = None

    # -- reports from any screen (K8, K14, K15) -------------------------------

    def _tool(self, ref: str):
        tool = self.conn.execute("SELECT * FROM tool_state WHERE asset_id = ? OR qr_code = ?", (ref.strip(), ref.strip())).fetchone()
        if not tool:
            raise Rejected("Pick a tool first")
        return tool

    def _reporter(self) -> str | None:
        who = (self.session and self.session.person) or self.instructor
        return who and who["person_id"]

    def report_damage(self, ref: str, severity: str, tags: list[str] | None = None, notes: str = "", photo: bool = True) -> dict:
        """K14 (and K8's "damaged tool"): Needs inspection or Safety hazard quarantines the tool until an instructor clears it."""
        if severity not in SEVERITIES:
            raise Rejected("Pick Minor, Needs inspection or Safety hazard")
        tool = self._tool(ref)
        pic = self._take_photo() if photo else None  # close-up: the student holds the damage up to the gate camera
        report = {"report_id": uuid.uuid4().hex, "asset_id": tool["asset_id"], "reported_by": self._reporter(),
                  "severity": severity, "tags": ", ".join(tags or []), "notes": (notes or "").strip()[:500],
                  "photo": pic, "opened": now_iso()}
        quarantine = severity != "minor" and tool["status"] == "available"
        with tx(self.conn):
            self.conn.execute("INSERT INTO damage_reports (report_id, asset_id, reported_by, severity, tags, notes, photo, opened)"
                              " VALUES (:report_id, :asset_id, :reported_by, :severity, :tags, :notes, :photo, :opened)", report)
            if pic:
                enqueue(self.conn, "photo", {"path": pic, "report_id": report["report_id"], "ts": report["opened"]})
            enqueue(self.conn, "damage", report)
            if quarantine:
                self.conn.execute("UPDATE tools SET status = 'quarantined' WHERE asset_id = ?", (tool["asset_id"],))
                enqueue(self.conn, "tool", {"asset_id": tool["asset_id"], "status": "quarantined"})
            self._open_alert("damage", asset_id=tool["asset_id"], person_id=report["reported_by"], photo=pic,
                             details=f"{severity.replace('_', ' ')}: {', '.join(filter(None, [report['tags'], report['notes']]))}")
        self._touch()
        return {"report_id": report["report_id"], "tool": tool["name"], "quarantined": quarantine or tool["status"] == "quarantined"}

    def report_missing_from_spot(self, ref: str, notes: str = "") -> dict:
        """K8: "this tool isn't in its spot" from a student, logged as an alert for staff."""
        tool = self._tool(ref)
        with tx(self.conn):
            alert = self._open_alert("not_in_spot", asset_id=tool["asset_id"], person_id=self._reporter(),
                                     details=("Reported at the kiosk. " + (notes or "")).strip())
        self._touch()
        return {"alert_id": alert["alert_id"], "tool": tool["name"]}

    def flag_label(self, ref: str, condition: str) -> dict:
        """K15: a worn label goes to the reprint queue; the tool stays usable."""
        if condition not in LABEL_CONDITIONS:
            raise Rejected("Pick Faded, Scratched, Peeling or Torn")
        tool = self._tool(ref)
        row = {"reprint_id": uuid.uuid4().hex, "asset_id": tool["asset_id"], "reported_by": self._reporter(),
               "condition": condition, "flagged_at": now_iso()}
        with tx(self.conn):
            self.conn.execute("INSERT INTO reprint_queue VALUES (:reprint_id, :asset_id, :reported_by, :condition, :flagged_at)", row)
            enqueue(self.conn, "reprint", {**row, "printed": "no"})
        self._touch()
        return {"reprint_id": row["reprint_id"], "tool": tool["name"]}

    def _open_alert(self, kind: str, asset_id: str | None = None, person_id: str | None = None, kit_id: str | None = None,
                    details: str = "", photo: str | None = None) -> dict:
        """Kiosk-raised alerts (reports, short kits, locked IDs). Call inside tx(). No spot_id, so the cage
        monitor's one-alert-per-spot rule never folds a report into a camera alert."""
        tool = asset_id and self.conn.execute(
            "SELECT t.name, s.label FROM tools t LEFT JOIN spots s ON s.spot_id = t.spot_id WHERE t.asset_id = ?", (asset_id,)).fetchone()
        kit = kit_id and self.conn.execute("SELECT k.name, s.label FROM kits k LEFT JOIN spots s ON s.spot_id = k.spot_id"
                                           " WHERE k.kit_id = ?", (kit_id,)).fetchone()
        person = person_id and self.conn.execute("SELECT name FROM people WHERE person_id = ?", (person_id,)).fetchone()
        alert = {"alert_id": uuid.uuid4().hex, "opened": now_iso(), "type": kind, "asset_id": asset_id,
                 "last_person_id": person_id, "snapshot_photo": photo, "details": details}
        self.conn.execute("INSERT INTO alerts (alert_id, opened, type, asset_id, last_person_id, snapshot_photo, details)"
                          " VALUES (:alert_id, :opened, :type, :asset_id, :last_person_id, :snapshot_photo, :details)", alert)
        payload = {**alert, "status": "open", "kit_id": kit_id,
                   "tool_name": (tool and tool["name"]) or (kit and kit["name"]),
                   "spot_label": (tool and tool["label"]) or (kit and kit["label"]),
                   "last_person_name": person and person["name"]}
        enqueue(self.conn, "alert", payload)
        return payload

    def _touch(self) -> None:
        if self.session:
            self.session.last_activity = self.clock()
        if self.instructor:
            self.instructor_seen = self.clock()

    # -- instructor mode (K7, K14, K16, K17) ---------------------------------------

    def _staff(self) -> dict:
        if not self.instructor:
            raise Rejected("Instructor: tap Instructor and enter your PIN first")
        self.instructor_seen = self.clock()
        return self.instructor

    def logout_instructor(self) -> None:
        self.instructor = self.link_for = None

    def start_for(self, person_id: str, action: str) -> dict:
        """K7: a student who forgot their PIN. The session is theirs; every record names the instructor."""
        inst = self._staff()
        s = self.start(None, action, person_id=person_id, override_by=inst)
        self._audit(inst, "Transactions", s.id, "person_id", None, person_id, "Student without their PIN; instructor started the session")
        return self._started(s)

    def add_by_asset(self, asset_id: str) -> dict:
        """K7: type the engraved asset number of a tool whose label won't read."""
        inst = self._staff()
        item = self.scan(asset_id, by_asset=True, override_by=inst)
        self._audit(inst, "Transactions", self.session.id, "asset_id", None, item["asset_id"], "Label would not scan; entered by hand")
        return item

    def assign_pin(self, person_id: str) -> dict:
        """Give a person a new random PIN (4 digits for students, 6 for staff), shown once on screen.
        Also how a forgotten PIN is replaced: the old one stops working."""
        inst = self._staff()
        person = self.conn.execute("SELECT * FROM people WHERE person_id = ?", (person_id,)).fetchone()
        if not person:
            raise Rejected("Pick a person first")
        pin, h = pins.new_pin(self.conn, person["role"])
        with tx(self.conn):
            self.conn.execute("UPDATE people SET pin_hash = ? WHERE person_id = ?", (h, person_id))
            enqueue(self.conn, "person", {"person_id": person_id, "pin_hash": h})
            self._audit(inst, "People", person_id, "pin_hash", None, None, "New PIN given at the kiosk", in_tx=True)
        return {"name": person["name"], "pin": pin}

    def link_tag(self, asset_id: str, uid: str | None = None) -> dict:
        """Pick a tool, then tap its new NFC sticker. With no uid yet, the next sticker tapped is the one."""
        inst = self._staff()
        tool = self._tool(asset_id)
        if not uid:
            self.link_for = dict(tool)
            return {"link": tool["name"], "waiting": True}
        uid = uid.strip()
        self.link_for = None
        clash = self.conn.execute("SELECT name FROM tools WHERE (nfc_uid = ? OR qr_code = ?) AND asset_id != ?",
                                  (uid, uid, tool["asset_id"])).fetchone()
        if clash:
            raise Rejected(f"That sticker already belongs to {clash['name']}")
        with tx(self.conn):
            self.conn.execute("UPDATE tools SET nfc_uid = ? WHERE asset_id = ?", (uid, tool["asset_id"]))
            enqueue(self.conn, "tool", {"asset_id": tool["asset_id"], "nfc_uid": uid})
            self._audit(inst, "Tools", tool["asset_id"], "nfc_uid", tool["nfc_uid"], uid, "NFC sticker linked at the kiosk", in_tx=True)
        return {"link": tool["name"], "nfc_uid": uid}

    def extend(self, asset_id: str, due: str, reason: str = "") -> dict:
        """K16: an instructor moves a loan's due time to a set date."""
        inst = self._staff()
        tool = self._tool(asset_id)
        if tool["last_action"] != "out":
            raise Rejected(f"{tool['name']} isn't checked out")
        try:
            new = datetime.fromisoformat(due)
        except ValueError:
            raise Rejected("Pick a date and time") from None
        if new.tzinfo is None:
            new = new.astimezone()  # typed on the kiosk: kiosk local time
        row = {"txn_id": tool["out_txn_id"], "due_at": new.astimezone(timezone.utc).isoformat(timespec="seconds"),
               "by": inst["person_id"], "ts": now_iso(), "reason": reason or "Extended at the kiosk"}
        with tx(self.conn):
            self.conn.execute("INSERT INTO loan_extensions VALUES (:txn_id, :due_at, :by, :ts, :reason)", row)
            enqueue(self.conn, "extension", {**row, "asset_id": tool["asset_id"]})
            self._audit(inst, "Transactions", tool["out_txn_id"], "due_at", tool["due_at"], row["due_at"], row["reason"], in_tx=True)
        return {"asset_id": tool["asset_id"], "due_at": row["due_at"]}

    def clear_quarantine(self, asset_id: str, note: str) -> dict:
        """K14: an instructor inspected the tool and it's fine to use again."""
        inst = self._staff()
        tool = self._tool(asset_id)
        if tool["status"] != "quarantined":
            raise Rejected(f"{tool['name']} isn't in quarantine")
        if not (note or "").strip():
            raise Rejected("Say what you checked")
        now = now_iso()
        with tx(self.conn):
            self.conn.execute("UPDATE tools SET status = 'available' WHERE asset_id = ?", (tool["asset_id"],))
            enqueue(self.conn, "tool", {"asset_id": tool["asset_id"], "status": "available"})
            for r in self.conn.execute("SELECT report_id FROM damage_reports WHERE asset_id = ? AND resolved_at IS NULL",
                                       (tool["asset_id"],)).fetchall():
                fix = {"report_id": r["report_id"], "resolved_by": inst["person_id"], "resolution": f"released: {note}", "resolved_at": now}
                self.conn.execute("UPDATE damage_reports SET resolved_by = :resolved_by, resolution = :resolution,"
                                  " resolved_at = :resolved_at WHERE report_id = :report_id", fix)
                enqueue(self.conn, "damage", fix)
            self._audit(inst, "Tools", tool["asset_id"], "status", "quarantined", "available", note, in_tx=True)
        return {"asset_id": tool["asset_id"], "status": "available"}

    def take_tool_photo(self, asset_id: str) -> dict:
        """Instructor holds a newly labeled tool up to the gate camera; the screen shows this photo from then on."""
        inst = self._staff()
        tool = self._tool(asset_id)
        path = self._take_photo()
        if not path:
            raise Rejected("The camera didn't take a photo. Try again.")
        with tx(self.conn):
            self.conn.execute("UPDATE tools SET photo = ? WHERE asset_id = ?", (path, tool["asset_id"]))
            enqueue(self.conn, "photo", {"path": path, "keep": True})  # kept on the Pi: the screen needs it offline
            enqueue(self.conn, "tool", {"asset_id": tool["asset_id"], "photo": path})
            self._audit(inst, "Tools", tool["asset_id"], "photo", tool["photo"], path, "Tool photo taken at the kiosk", in_tx=True)
        return {"asset_id": tool["asset_id"], "name": tool["name"]}

    def find_people(self, q: str = "") -> list[dict]:
        self._staff()
        like = f"%{q.strip()}%"
        rows = self.conn.execute("SELECT person_id, name, role FROM people WHERE active = 1 AND (name LIKE ? OR person_id LIKE ?)"
                                 " ORDER BY name LIMIT 50", (like, like)).fetchall()
        return [dict(r) for r in rows]

    def _audit(self, who: dict, tab: str, row: str, fld: str, old, new, reason: str, in_tx: bool = False) -> None:
        """Every override is logged with the instructor's name (K7, PRD Audit)."""
        entry = {"ts": now_iso(), "who": who["name"], "tab": tab, "row": row, "field": fld,
                 "old_value": None if old is None else str(old), "new_value": None if new is None else str(new), "reason": reason}

        def write():
            self.conn.execute("INSERT INTO audit_log (ts, who, tab, row, field, old_value, new_value, reason)"
                              " VALUES (:ts, :who, :tab, :row, :field, :old_value, :new_value, :reason)", entry)
            enqueue(self.conn, "audit", entry)

        if in_tx:
            write()
        else:
            with tx(self.conn):
                write()

    # -- idle timeout (K9) -------------------------------------------------

    def seconds_left(self) -> float | None:
        if not self.session:
            return None
        return max(0.0, self.idle_timeout - (self.clock() - self.session.last_activity))

    def expire_idle(self) -> dict | None:
        """Call about once a second. A walked-away session is saved, not dropped: the tools left with them.
        An instructor who walks away is logged out."""
        if self.instructor and self.clock() - self.instructor_seen > self.idle_timeout * 2:
            self.logout_instructor()
        left = self.seconds_left()
        if left is None or left > 0:
            return None
        return self.finish(force=True)

    # -- helpers ------------------------------------------------------------

    def _current(self) -> Session:
        if not self.session:
            raise Rejected("Enter your PIN to start")
        return self.session

    def _take_photo(self) -> str | None:
        if not self.camera:
            return None
        try:
            return self.camera.capture()
        except Exception:  # a camera fault must never block a checkout
            log.exception("gate photo failed")
            return None

    def state(self) -> dict:
        """What the screen needs to draw, besides the session itself."""
        return {
            "next_action": self.next_action,
            "instructor": self.instructor and self.instructor["name"],
            "link_for": self.link_for and self.link_for["name"],
        }


def kits_view(s: Session) -> list[dict]:
    return [{"kit_id": k, "name": v["name"], "mode": v["mode"], "confirmed": v["confirmed"],
             "waiting_for": sorted(v["expect"] - v["got"])} for k, v in s.kits.items()]
