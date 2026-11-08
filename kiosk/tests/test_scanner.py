import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from lab_toolbox import reader
from lab_toolbox.app import create_app
from lab_toolbox.reader import Watcher
from lab_toolbox.sessions import Kiosk

BLANK = np.full((480, 640), 120, dtype=np.uint8)
BUSY = BLANK.copy()
BUSY[:, :320] = 20  # a hand coming into view


def test_watcher_only_decodes_after_motion(clock):
    seen = []
    w = Watcher(decode=lambda f: seen.append(1) or (["T-0001"] if f is BUSY else []), clock=clock)
    assert w.feed(BLANK) == [] and w.feed(BLANK) == [] and not seen  # idle: no decoding at all
    assert w.pause() == reader.IDLE_S
    assert w.feed(BUSY) == ["T-0001"] and w.pause() == 0  # motion: decode, go full rate
    for _ in range(10):  # label held perfectly still: no motion, but each read keeps it hot
        clock.t += 1
        assert w.feed(BUSY) == []  # and it still counts as one scan
    assert w.hot()
    w.feed(BLANK)  # put away
    clock.t += reader.HOT_S + 1
    assert w.feed(BLANK) == [] and not w.hot()  # back to idle


def test_kiosk_handle_routes_id_then_tools(conn, clock):
    k = Kiosk(conn, "gate-1", clock=clock)
    assert k.handle("ID-1001")["person"] == "Jordan"
    assert k.session.action == "out"
    assert k.handle("T-0001")["asset_id"] == "HHA-0001"
    k.finish()
    k.next_action = "in"
    k.handle("ID-1001")
    assert k.session.action == "in" and k.next_action == "out"  # one Return tap covers one session


class FakeBuzzer:
    def __init__(self):
        self.played = []

    def play(self, sound):
        self.played.append(sound)


class FakeCamera:
    """Shows a blank view, then each code's QR for a few frames, then blank."""

    def __init__(self, codes, tmp_path):
        zxingcpp = pytest.importorskip("zxingcpp")
        self.frames = [BLANK] * 2
        for c in codes:
            qr = np.asarray(zxingcpp.write_barcode_to_image(zxingcpp.create_barcode(c, zxingcpp.BarcodeFormat.QRCode), scale=6))
            f = BLANK.copy()
            f[40:40 + qr.shape[0], 40:40 + qr.shape[1]] = qr
            self.frames += [f] * 3 + [BLANK] * 2
        self.tmp_path = tmp_path

    def frame(self):
        time.sleep(0.01)
        return self.frames.pop(0) if self.frames else BLANK

    def capture(self):
        p = self.tmp_path / "gate.jpg"
        p.write_bytes(b"jpg")
        return str(p)


def test_camera_scans_reach_the_kiosk(conn, tmp_path):
    buzzer = FakeBuzzer()
    cam = FakeCamera(["ID-1001", "T-0001", "NOPE"], tmp_path)
    with TestClient(create_app(Kiosk(conn, "gate-1"), camera=cam, buzzer=buzzer)) as c:
        deadline = time.monotonic() + 5
        while (cam.frames or len(buzzer.played) < 3) and time.monotonic() < deadline:
            time.sleep(0.05)
        st = c.get("/api/session").json()
        assert st["session"]["person"] == "Jordan"
        assert [i["asset_id"] for i in st["session"]["items"]] == ["HHA-0001"]
        assert buzzer.played == ["accepted", "accepted", "rejected"]
        assert st["last_event"]["ok"] is False and "Unknown code" in st["last_event"]["text"]
        saved = c.post("/api/session/finish").json()
        assert saved["photo"].endswith("gate.jpg") and buzzer.played[-1] == "saved"


def test_code_endpoint_and_mode(conn):
    buzzer = FakeBuzzer()
    with TestClient(create_app(Kiosk(conn, "gate-1"), buzzer=buzzer)) as c:
        assert c.post("/api/mode", json={"action": "sideways"}).status_code == 409
        c.post("/api/mode", json={"action": "out"})
        assert c.post("/api/code", json={"code": "ID-1001"}).json()["person"] == "Jordan"
        assert c.post("/api/code", json={"code": "ID-1001"}).status_code == 409  # ID during a session
        assert buzzer.played == ["accepted", "rejected"]
