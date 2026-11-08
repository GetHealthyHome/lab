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
