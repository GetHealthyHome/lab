from fastapi.testclient import TestClient

from lab_toolbox.app import create_app
from lab_toolbox.sessions import Kiosk


def test_checkout_over_http(conn, clock):
    with TestClient(create_app(Kiosk(conn, "gate-1", clock=clock))) as c:
        assert c.get("/").status_code == 200
        assert c.post("/api/session", json={"id_code": "ID-1001", "action": "out"}).json()["person"] == "Jordan"
        assert c.post("/api/session/scan", json={"code": "T-0001"}).json()["asset_id"] == "HHA-0001"
        r = c.post("/api/session/scan", json={"code": "NOPE"})
        assert r.status_code == 409 and "Unknown code" in r.json()["reason"]
        st = c.get("/api/session").json()
        assert st["seconds_left"] == 45 and len(st["session"]["items"]) == 1
        assert len(c.post("/api/session/finish").json()["items"]) == 1
        assert c.get("/api/health").json()["sync_pending"] == 1
        assert c.get("/api/session").json()["theme"] == "dark"
        conn.execute("INSERT INTO settings VALUES ('screen_theme', 'light')")  # as pulled from the dashboard
        assert c.get("/api/session").json()["theme"] == "light"


def test_screen_gets_battery_and_kit_piece_names(conn, clock):
    from lab_toolbox.seed import SCREEN_DEMO
    conn.executescript(SCREEN_DEMO)
    with TestClient(create_app(Kiosk(conn, "gate-1", clock=clock))) as c:
        assert c.get("/static/logo.svg").status_code == 200
        c.post("/api/session", json={"id_code": "ID-1001", "action": "out"})
        c.post("/api/session/scan", json={"code": "T-0003"})
        s = c.get("/api/session").json()["session"]
        assert s["battery_options"]["HHA-0003"]["available"][0]["spot"] == "Shelf 02, bay 01, charger 01"
        c.post("/api/session/scan", json={"code": "T-0004"})
        c.post("/api/session/scan", json={"code": "K-0001"})
        c.post("/api/session/finish")
        c.post("/api/session", json={"id_code": "ID-1001", "action": "in"})
        c.post("/api/session/scan", json={"code": "K-0001"})
        kit = c.get("/api/session").json()["session"]["kits"][0]
        assert [p["name"] for p in kit["waiting"]] == ["Flare tool", "Tubing cutter"]


def test_starts_without_gate_camera_or_buzzer(conn, clock, monkeypatch):
    # no picamera2/gpiozero here, like a Pi whose camera ribbon or buzzer isn't connected yet
    monkeypatch.setenv("GATE_CAMERA", "pi")
    monkeypatch.setenv("BUZZER_PIN", "18")
    with TestClient(create_app(Kiosk(conn, "gate-1", clock=clock))) as c:
        assert c.get("/api/health").status_code == 200


def test_screen_dims_after_closing_until_someone_touches_it(conn, clock, tmp_path):
    from datetime import datetime

    from lab_toolbox import loans
    from lab_toolbox.app import DIM_AFTER_SECONDS, dim_tick
    from lab_toolbox.hardware import Backlight

    night, day = datetime(2026, 1, 5, 23, 0).astimezone(), datetime(2026, 1, 5, 12, 0).astimezone()
    assert loans.closed(conn, night) and not loans.closed(conn, day)
    assert loans.closed(conn, datetime(2026, 1, 5, 6, 59).astimezone())
    assert not loans.closed(conn, datetime(2026, 1, 5, 7, 0).astimezone())
    conn.execute("INSERT INTO settings VALUES ('lab_open', '08:00'), ('lab_close', '01:00')")  # open past midnight
    assert not loans.closed(conn, datetime(2026, 1, 5, 0, 30).astimezone())
    assert loans.closed(conn, datetime(2026, 1, 5, 3, 0).astimezone())
    conn.execute("UPDATE settings SET value = 'late' WHERE key = 'lab_close'")  # mistyped: back to 21:00
    assert loans.closed(conn, night) and not loans.closed(conn, day)

    panel = tmp_path / "10-0045"
    panel.mkdir()
    (panel / "max_brightness").write_text("255")
    (panel / "brightness").write_text("25")  # left dim by a restart in the night
    level = lambda: (panel / "brightness").read_text()
    app = create_app(Kiosk(conn, "gate-1", clock=clock), backlight=Backlight(str(tmp_path)))
    with TestClient(app) as c:
        t0 = app.state.last_touch
        dim_tick(app, night, t0 + DIM_AFTER_SECONDS - 1)
        assert level() == "255"  # someone was here a moment ago (and the first tick always sets the level)
        dim_tick(app, day, t0 + DIM_AFTER_SECONDS)
        assert level() == "255"  # never during opening hours
        dim_tick(app, night, t0 + DIM_AFTER_SECONDS)
        assert level() == "25"
        c.post("/api/wake")  # a touch
        dim_tick(app, night, app.state.last_touch + 1)
        assert level() == "255"
        c.post("/api/session", json={"id_code": "ID-1001", "action": "out"})
        dim_tick(app, night, app.state.last_touch + DIM_AFTER_SECONDS)
        assert level() == "255"  # a session in progress keeps it bright
    Backlight(str(tmp_path / "none")).dim(True)  # a screen with no backlight file: nothing to do, no error
