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


def test_camera_reads_labels_only_after_the_qr_button(conn, tmp_path):
    buzzer = FakeBuzzer()
    cam = FakeCamera(["T-0001", "NOPE"], tmp_path)
    kiosk = Kiosk(conn, "gate-1")
    kiosk.start("ID-1001", "out")
    with TestClient(create_app(kiosk, camera=cam, buzzer=buzzer)) as c:
        before = len(cam.frames)
        time.sleep(0.3)
        assert len(cam.frames) == before and not c.get("/api/session").json()["qr"]  # NFC first: camera not reading
        assert c.post("/api/qr").json()["seconds"] == 60
        assert c.get("/api/session").json()["qr"]
        deadline = time.monotonic() + 5
        while (cam.frames or len(buzzer.played) < 2) and time.monotonic() < deadline:
            time.sleep(0.05)
        st = c.get("/api/session").json()
        assert [i["asset_id"] for i in st["session"]["items"]] == ["HHA-0001"]
        assert buzzer.played == ["accepted", "rejected"]
        assert st["last_event"]["ok"] is False and "Unknown code" in st["last_event"]["text"]
        saved = c.post("/api/session/finish").json()
        assert saved["photo"].endswith("gate.jpg") and buzzer.played[-1] == "saved"


class FakeNfc:
    def __init__(self, reads):
        self.reads = reads

    def read(self):
        time.sleep(0.01)
        return self.reads.pop(0) if self.reads else None


def test_nfc_taps_reach_the_kiosk_once_each(conn):
    buzzer = FakeBuzzer()
    conn.execute("UPDATE tools SET nfc_uid = '04AA' WHERE asset_id = 'HHA-0001'")
    kiosk = Kiosk(conn, "gate-1")
    kiosk.start("ID-1001", "out")
    nfc = FakeNfc(["04AA", "04AA", "04AA", None, "04FF"])  # sticker held on the reader for three reads
    with TestClient(create_app(kiosk, buzzer=buzzer, nfc=nfc)) as c:
        deadline = time.monotonic() + 5
        while (nfc.reads or len(buzzer.played) < 2) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert [i["asset_id"] for i in c.get("/api/session").json()["session"]["items"]] == ["HHA-0001"]
        assert buzzer.played == ["accepted", "rejected"]


def test_code_endpoint_and_mode(conn):
    buzzer = FakeBuzzer()
    with TestClient(create_app(Kiosk(conn, "gate-1"), buzzer=buzzer)) as c:
        assert c.post("/api/mode", json={"action": "sideways"}).status_code == 409
        r = c.post("/api/code", json={"code": "T-0001"})
        assert r.status_code == 409 and "Enter your PIN" in r.json()["reason"]
        assert buzzer.played == ["rejected"]
