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
from pydantic import BaseModel

from . import db, sync
from .db import now_iso
from .hardware import Buzzer
from .reader import Watcher
from .sessions import Kiosk, Rejected

log = logging.getLogger(__name__)


class StartBody(BaseModel):
    id_code: str
    action: str  # "out" | "in"


class ScanBody(BaseModel):
    code: str


class ModeBody(BaseModel):
    action: str  # "out" | "in"


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
        return JSONResponse({"reason": e.reason}, status_code=409)

    def k(request: Request) -> Kiosk:
        return request.app.state.kiosk

    @app.get("/")
    async def screen():
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @app.get("/api/session")
    async def get_session(request: Request):
        kiosk = k(request)
        s = kiosk.session
        return {
            "session": s and {"id": s.id, "action": s.action, "person": s.person["name"], "items": s.items},
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
        if body.action not in ("out", "in"):
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
        s = k(request).start(body.id_code, body.action)
        return {"id": s.id, "action": s.action, "person": s.person["name"]}

    @app.post("/api/session/scan")
    async def scan(body: ScanBody, request: Request):
        return k(request).scan(body.code)

    @app.post("/api/session/finish")
    async def finish(request: Request):
        saved = k(request).finish()
        if saved["items"]:
            request.app.state.buzzer.play("saved")
        return saved

    @app.delete("/api/session")
    async def cancel(request: Request):
        k(request).cancel()
        return {"ok": True}

    @app.get("/api/health")
    async def health(request: Request):
        return {"kiosk_id": k(request).kiosk_id, "sync_pending": sync.pending(k(request).conn)}

    return app


def handle_code(app: FastAPI, code: str) -> dict:
    """K1 feedback: every code gets a sound and an event the screen can show. Refusals still raise Rejected."""
    seq = (app.state.last_event or {"seq": 0})["seq"] + 1
    try:
        result = app.state.kiosk.handle(code)
    except Rejected as e:
        app.state.buzzer.play("rejected")
        app.state.last_event = {"seq": seq, "ok": False, "text": e.reason, "at": now_iso()}
        raise
    app.state.buzzer.play("accepted")
    text = f"Hi, {result['person']}" if "person" in result else f"Added {result['name']}"
    app.state.last_event = {"seq": seq, "ok": True, "text": text, "at": now_iso()}
    return result


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
