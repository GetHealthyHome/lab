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
