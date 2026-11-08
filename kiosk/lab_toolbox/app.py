"""Kiosk web service. The screen (whatever it ends up looking like) only talks to this JSON API.

Run: KIOSK_DB=kiosk.db uvicorn lab_toolbox.app:app
"""
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import db, sync
from .sessions import Kiosk, Rejected


class StartBody(BaseModel):
    id_code: str
    action: str  # "out" | "in"


class ScanBody(BaseModel):
    code: str


def create_app(kiosk: Kiosk | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.kiosk is None:
            conn = db.connect(os.environ.get("KIOSK_DB", "kiosk.db"))
            app.state.kiosk = Kiosk(conn, os.environ.get("KIOSK_ID", "gate-1"))
        app.state.last_saved = None
        task = asyncio.create_task(_idle_loop(app))
        yield
        task.cancel()

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
        }

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
        return k(request).finish()

    @app.delete("/api/session")
    async def cancel(request: Request):
        k(request).cancel()
        return {"ok": True}

    @app.get("/api/health")
    async def health(request: Request):
        return {"kiosk_id": k(request).kiosk_id, "sync_pending": sync.pending(k(request).conn)}

    return app


async def _idle_loop(app: FastAPI):
    while True:
        await asyncio.sleep(1)
        saved = app.state.kiosk.expire_idle()
        if saved:
            app.state.last_saved = saved


app = create_app()
