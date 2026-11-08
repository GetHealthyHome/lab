"""Kiosk web service. The screen (whatever it ends up looking like) only talks to this JSON API.

Run: KIOSK_DB=kiosk.db uvicorn lab_toolbox.app:app
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, sync
from .db import now_iso
from .hardware import Buzzer
from .reader import Watcher
from . import loans
from .sessions import Kiosk, Rejected, kits_view

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class StartBody(BaseModel):
    id_code: str
    action: str  # "out" | "in"


class ScanBody(BaseModel):
    code: str


class ModeBody(BaseModel):
    action: str  # "out" | "in" | "instructor"


class PinBody(BaseModel):
    pin: str


class AssetBody(BaseModel):
    asset_id: str


class KitBody(BaseModel):
    kit_id: str
    missing: list[str] = []


class DamageBody(BaseModel):
    asset_id: str  # asset number or label code
    severity: str  # minor | needs_inspection | safety_hazard
    tags: list[str] = []
    notes: str = ""
    photo: bool = True


class ProblemBody(BaseModel):
    asset_id: str
    notes: str = ""


class LabelBody(BaseModel):
    asset_id: str
    condition: str  # Faded | Scratched | Peeling | Torn


class ForBody(BaseModel):
    person_id: str
    action: str = "out"


class EnrollBody(BaseModel):
    person_id: str
    code: str | None = None  # leave out to take the next scanned card


class ExtendBody(BaseModel):
    asset_id: str
    due_at: str  # ISO date-time; no zone means kiosk local time
    reason: str = ""


class ClearBody(BaseModel):
    asset_id: str
    note: str


def create_app(kiosk: Kiosk | None = None, camera=None, buzzer: Buzzer | None = None) -> FastAPI:
    """camera: anything with frame() and capture() (see hardware.PiGateCamera). With one, the app reads codes itself."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.kiosk is None:
            conn = db.connect(os.environ.get("KIOSK_DB", "kiosk.db"))
            app.state.kiosk = Kiosk(conn, os.environ.get("KIOSK_ID", "gate-1"))
        cam = camera
        if cam is None and os.environ.get("GATE_CAMERA") == "pi":
            from .hardware import PiGateCamera

            cam = PiGateCamera(os.environ.get("PHOTO_DIR", "photos"))
        pin = os.environ.get("BUZZER_PIN")
        app.state.buzzer = buzzer or Buzzer(int(pin) if pin else None)
        app.state.last_saved = None
        app.state.last_event = None
        tasks = [asyncio.create_task(_idle_loop(app))]
        if cam is not None:
            app.state.kiosk.camera = cam
            tasks.append(asyncio.create_task(_scan_loop(app, cam)))
        yield
        for t in tasks:
            t.cancel()

    app = FastAPI(title="Lab Toolbox kiosk", lifespan=lifespan)
    app.state.kiosk = kiosk

    @app.exception_handler(Rejected)
    async def rejected(_: Request, e: Rejected):
        # kind lets the screen pick the right look: rejected (red), wrong_battery (amber), locked (lock screen)
        return JSONResponse({"reason": e.reason, "kind": e.sound, **e.details}, status_code=409)

    def k(request: Request) -> Kiosk:
        return request.app.state.kiosk

    @app.get("/")
    async def screen():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")  # logo.png / logo.svg

    @app.get("/api/session")
    async def get_session(request: Request):
        kiosk = k(request)
        s = kiosk.session
        return {
            "session": s and {"id": s.id, "action": s.action, "person": s.person["name"], "items": s.items,
                              "pin": s.pin, "kits": _kits_named(kiosk, s),
                              "started_by": s.override_by and s.override_by["name"],
                              # K12: which batteries fit each tool still waiting for one, and where the charged ones sit
                              "battery_options": {i["asset_id"]: kiosk._battery_options(i["needs_battery"])
                                                  for i in s.items if i.get("needs_battery") and not i.get("battery")}},
            **kiosk.state(),
            "seconds_left": kiosk.seconds_left(),
            "idle_timeout": kiosk.idle_timeout,
            # set when the idle timer saved a session, so the screen can still show the summary
            "auto_saved": request.app.state.last_saved,
            # latest camera scan result, so the screen can flash green or red; `seq` changes on each one
            "last_event": request.app.state.last_event,
        }

    @app.post("/api/mode")
    async def mode(body: ModeBody, request: Request):
        """The screen's Check out / Return buttons: which kind of session the next ID scan starts."""
        if body.action not in ("out", "in", "instructor"):
            raise Rejected("Choose Check out or Return")
        k(request).next_action = body.action
        return {"next_action": body.action}

    @app.post("/api/code")
    async def code(body: ScanBody, request: Request):
        """Same path as a camera scan (ID or tool, with sound); also takes a USB barcode scanner's input."""
        return handle_code(request.app, body.code)

    @app.post("/api/session")
    async def start(body: StartBody, request: Request):
        request.app.state.last_saved = None
        kiosk = k(request)
        return kiosk._started(kiosk.start(body.id_code, body.action))

    @app.post("/api/session/scan")
    async def scan(body: ScanBody, request: Request):
        return k(request).scan(body.code)

    @app.post("/api/pin")
    async def pin(body: PinBody, request: Request):
        """K11: answers the PIN prompt on screen (a student's, or an instructor logging in)."""
        return sounded(request.app, lambda: k(request).enter_pin(body.pin))

    @app.post("/api/session/remove")
    async def remove(body: AssetBody, request: Request):
        return k(request).remove(body.asset_id)

    @app.post("/api/session/kit")
    async def confirm_kit(body: KitBody, request: Request):
        """K13 return: confirm the kit's pieces; `missing` lists the ones that aren't back (standard kits)."""
        return k(request).confirm_kit(body.kit_id, body.missing)

    @app.post("/api/damage")
    async def damage(body: DamageBody, request: Request):
        return k(request).report_damage(body.asset_id, body.severity, body.tags, body.notes, body.photo)

    @app.post("/api/problem")
    async def problem(body: ProblemBody, request: Request):
        """K8: tool not in its spot."""
        return k(request).report_missing_from_spot(body.asset_id, body.notes)

    @app.post("/api/label")
    async def label(body: LabelBody, request: Request):
        return k(request).flag_label(body.asset_id, body.condition)

    @app.get("/api/tools")
    async def tools(request: Request, q: str = ""):
        """Pick a tool by name (for reports and worn labels, when the label won't scan)."""
        like = f"%{q.strip()}%"
        rows = k(request).conn.execute(
            "SELECT asset_id, name, state, spot_id FROM tool_state WHERE state != 'retired' AND (name LIKE ? OR asset_id LIKE ?)"
            " ORDER BY name LIMIT 50", (like, like)).fetchall()
        return [dict(r) for r in rows]

    @app.get("/api/loans")
    async def get_loans(request: Request, person_id: str | None = None):
        """K16: tools out with due times; `overdue` ones show red."""
        kiosk = k(request)
        return loans.loans(kiosk.conn, kiosk.wall(), person_id)

    # -- instructor mode (K7, K14, K16, K17) --------------------------------

    @app.get("/api/instructor/people")
    async def people(request: Request, q: str = ""):
        return k(request).find_people(q)

    @app.post("/api/instructor/start")
    async def start_for(body: ForBody, request: Request):
        request.app.state.last_saved = None
        return k(request).start_for(body.person_id, body.action)

    @app.post("/api/instructor/manual")
    async def manual(body: AssetBody, request: Request):
        return sounded(request.app, lambda: k(request).add_by_asset(body.asset_id))

    @app.post("/api/instructor/enroll")
    async def enroll(body: EnrollBody, request: Request):
        return k(request).enroll(body.person_id, body.code)

    @app.post("/api/instructor/extend")
    async def extend(body: ExtendBody, request: Request):
        return k(request).extend(body.asset_id, body.due_at, body.reason)

    @app.post("/api/instructor/clear")
    async def clear(body: ClearBody, request: Request):
        return k(request).clear_quarantine(body.asset_id, body.note)

    @app.post("/api/instructor/logout")
    async def logout(request: Request):
        k(request).logout_instructor()
        return {"ok": True}

    @app.post("/api/session/finish")
    async def finish(request: Request):
        saved = k(request).finish()  # refuses while a battery or kit check is unfinished (reason on screen)
        if saved["items"]:
            request.app.state.buzzer.play("saved")
        return saved

    @app.delete("/api/session")
    async def cancel(request: Request):
        k(request).cancel()
        return {"ok": True}

    @app.get("/api/health")
    async def health(request: Request):
        conn = k(request).conn
        last_snapshot = conn.execute("SELECT MAX(ts) FROM snapshots").fetchone()[0]  # D8
        return {"kiosk_id": k(request).kiosk_id, "sync_pending": sync.pending(conn), "last_snapshot": last_snapshot}

    return app


def _kits_named(kiosk: Kiosk, s) -> list[dict]:
    """kits_view plus piece names, so the screen can list what still has to come back (K13)."""
    kits = kits_view(s)
    ids = sorted({a for k in kits for a in k["waiting_for"]})
    names = dict(kiosk.conn.execute(f"SELECT asset_id, name FROM tools WHERE asset_id IN ({','.join('?' * len(ids))})",
                                    ids).fetchall()) if ids else {}
    for k in kits:
        k["waiting"] = [{"asset_id": a, "name": names.get(a, a)} for a in k["waiting_for"]]
    return kits


def handle_code(app: FastAPI, code: str) -> dict:
    """K1 feedback: every code gets a sound and an event the screen can show. Refusals still raise Rejected."""
    return sounded(app, lambda: app.state.kiosk.handle(code))


def sounded(app: FastAPI, act) -> dict:
    """Run one kiosk action with the PRD's feedback: a sound plus an event (`kind` picks the look)."""
    seq = (app.state.last_event or {"seq": 0})["seq"] + 1
    try:
        result = act()
    except Rejected as e:
        app.state.buzzer.play(e.sound)
        app.state.last_event = {"seq": seq, "ok": False, "kind": e.sound, "text": e.reason, "at": now_iso(), **e.details}
        raise
    app.state.buzzer.play("accepted")
    app.state.last_event = {"seq": seq, "ok": True, "kind": "accepted", "text": _say(result), "at": now_iso()}
    return result


def _say(r: dict) -> str:
    if r.get("pin") in ("check", "setup"):
        return f"Hi, {r['person']}. " + ("Enter your PIN" if r["pin"] == "check" else "Choose a 4-digit PIN")
    if "instructor_pin" in r:
        return f"{r['name']}: " + ("enter your PIN" if r["instructor_pin"] == "check" else "choose a 4-digit PIN")
    if "instructor" in r:
        return f"Instructor mode: {r['instructor']}"
    if "enroll" in r:
        return f"Scan {r['enroll']}'s new card" if r.get("waiting") else f"Card linked to {r['enroll']}"
    if "person" in r and "id" in r:
        return f"Hi, {r['person']}"
    if "pieces" in r:
        return f"Added {r['name']}"
    if r.get("battery_needed"):
        return f"Added {r['name']}. Now scan a {r['battery_needed']['group']} battery"
    if r.get("paired_to"):
        return f"Battery paired with {r['paired_to']}"
    if "pin" in r:
        return f"Hi, {r['person']}"
    return f"Added {r.get('name', '')}".strip()


async def _scan_loop(app: FastAPI, camera):
    """Grab and decode on a worker thread; handle codes here on the event loop, so the Kiosk is only touched from one thread."""
    watcher = Watcher()
    while True:
        try:
            frame = await asyncio.to_thread(camera.frame)
            codes = await asyncio.to_thread(watcher.feed, frame)
        except Exception:  # a camera glitch must not kill scanning for the rest of the day
            log.exception("gate camera read failed")
            await asyncio.sleep(1)
            continue
        for c in codes:
            try:
                handle_code(app, c)
            except Rejected:
                pass  # already shown and buzzed
        await asyncio.sleep(watcher.pause())


async def _idle_loop(app: FastAPI):
    while True:
        await asyncio.sleep(1)
        saved = app.state.kiosk.expire_idle()
        if saved:
            app.state.last_saved = saved
            if saved["items"]:
                app.state.buzzer.play("saved")


app = create_app()
