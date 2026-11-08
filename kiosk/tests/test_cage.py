"""Cage monitor with a fake webcam that "photographs" whatever shelf picture the test sets."""
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from PIL import Image

from lab_toolbox import sync
from lab_toolbox.cage import Monitor, interval
from lab_toolbox.sessions import Kiosk

A, B = "LOC-S03-B02-H04", "LOC-S01-B01-NA"  # multimeter HHA-0001, gauge set HHA-0002 (seed data)
BOXES = {A: (10, 10, 30, 30), B: (60, 60, 30, 30)}
T0 = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)  # before any kiosk transaction (they use the real clock)


def shelf(*present):
    img = np.full((100, 100), 80, dtype=np.uint8)
    for spot in present:
        x, y, w, h = BOXES[spot]
        img[y:y + h, x:x + w] = 200
    return img


class FakeWebcam:
    def __init__(self):
        self.picture = shelf(A, B)
        self.broken = False

    def snapshot(self, path):
        if self.broken:
            raise RuntimeError("no picture from /dev/video0")
        Image.fromarray(self.picture).save(path)
        return path


@pytest.fixture
def cam():
    return FakeWebcam()


@pytest.fixture
def mon(conn, tmp_path, cam):
    ref = tmp_path / "reference.png"
    Image.fromarray(shelf(A, B)).save(ref)
    for spot, box in BOXES.items():
        conn.execute("UPDATE spots SET box = ?, reference_photo = ? WHERE spot_id = ?", (json.dumps(box), str(ref), spot))
    return Monitor(conn, cam, str(tmp_path / "photos"))


def queued(conn, kind):
    return [json.loads(r[0]) for r in conn.execute("SELECT payload FROM sync_queue WHERE kind = ? ORDER BY id", (kind,))]


def test_interval_comes_from_settings(conn):
    assert interval(conn) == timedelta(hours=1)
    conn.execute("INSERT INTO settings VALUES ('snapshot_interval_hours', '4')")
    assert interval(conn) == timedelta(hours=4)
    for bad in ("soon", "0", "-2"):
        conn.execute("UPDATE settings SET value = ? WHERE key = 'snapshot_interval_hours'", (bad,))
        assert interval(conn) == timedelta(hours=1)


def test_schedule_every_x_hours(conn, mon):
    conn.execute("INSERT INTO settings VALUES ('snapshot_interval_hours', '3')")
    assert mon.due(T0) == "scheduled"  # never taken one
    mon.take(T0, "scheduled")
    assert mon.due(T0 + timedelta(hours=2, minutes=59)) is None
    assert mon.due(T0 + timedelta(hours=3)) == "scheduled"


def test_after_return_waits_for_put_away(conn, mon, clock):
    k = Kiosk(conn, "gate-1", clock=clock)
    k.start("ID-1001", "out"); k.scan("T-0001"); k.finish()
    k.start("ID-1001", "in"); k.scan("T-0001"); k.finish()
    returned = datetime.fromisoformat(conn.execute("SELECT MAX(ts) FROM transactions").fetchone()[0])
    mon.take(returned - timedelta(minutes=5), "scheduled")  # the last scheduled one was before the return
    assert mon.due(returned + timedelta(seconds=30)) is None  # still walking to the shelf
    assert mon.due(returned + timedelta(minutes=2)) == "after_return"
    mon.take(returned + timedelta(minutes=2), "after_return")
    assert mon.due(returned + timedelta(minutes=3)) is None


def test_on_demand_from_dashboard(conn, mon):
    mon.take(T0, "scheduled")
    conn.execute("INSERT INTO settings VALUES ('snapshot_request', '2026-11-09 10:05')")  # pulled from the Sheet
    assert mon.due(T0 + timedelta(minutes=1)) == "on_demand"
    mon.take(T0 + timedelta(minutes=1), "on_demand")
    assert mon.due(T0 + timedelta(minutes=2)) is None


def test_hand_reaching_in_is_not_an_alert(conn, mon, cam):
    mon.take(T0, "scheduled")
    cam.picture = shelf(B)  # someone's hand / the tool lifted for a moment
    r = mon.take(T0 + timedelta(hours=1), "scheduled")
    assert r["spots"][A] == "empty" and r["alerts"] == []
    assert mon.due(T0 + timedelta(hours=1, minutes=1)) == "confirm"  # look again soon
    cam.picture = shelf(A, B)
    assert mon.take(T0 + timedelta(hours=1, minutes=1), "confirm")["alerts"] == []
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0


def test_missing_tool_alert_names_last_borrower_and_updates_not_repeats(conn, mon, cam, clock, tmp_path):
    gate = tmp_path / "gate.jpg"
    gate.write_bytes(b"jpg")
    k = Kiosk(conn, "gate-1", camera=type("C", (), {"capture": lambda self: str(gate)})(), clock=clock)
    k.start("ID-1002", "out"); k.scan("T-0001"); k.finish()
    k.start("ID-1002", "in"); k.scan("T-0001"); k.finish()  # "returned"... but never hung back up

    cam.picture = shelf(B)
    assert mon.take(T0, "after_return")["alerts"] == []
    (alert,) = mon.take(T0 + timedelta(minutes=1), "confirm")["alerts"]
    assert alert["type"] == "missing_tool" and alert["spot_id"] == A and alert["asset_id"] == "HHA-0001"
    assert alert["tool_name"] == "Digital multimeter" and alert["spot_label"] == "Shelf 03, bay 02, hook 04"
    assert alert["last_person_id"] == "P-2" and alert["last_person_name"] == "Sam"
    assert alert["gate_photo"] == str(gate) and alert["last_checkout"]
    r, g, b = (int(v) for v in Image.open(alert["snapshot_photo"]).convert("RGB").getpixel((25, 10)))
    assert r > 150 and g < 80 and b < 80  # red box drawn on the spot's edge

    (again,) = mon.take(T0 + timedelta(hours=1), "scheduled")["alerts"]  # C6
    assert again["alert_id"] == alert["alert_id"] and again["opened"] == alert["opened"]
    assert conn.execute("SELECT COUNT(*), MAX(last_seen) FROM alerts").fetchone()[1] == again["last_seen"]
    assert len(queued(conn, "alert")) == 2 and len(queued(conn, "snapshot")) == 3

    conn.execute("UPDATE alerts SET status = 'resolved'")  # staff closed it on the dashboard
    (fresh,) = mon.take(T0 + timedelta(hours=2), "scheduled")["alerts"]
    assert fresh["alert_id"] != alert["alert_id"]


def test_returned_without_scanning(conn, mon, clock):
    k = Kiosk(conn, "gate-1", clock=clock)
    k.start("ID-1001", "out"); k.scan("T-0002"); k.finish()  # gauge set out, but camera still sees it
    mon.take(T0, "scheduled")
    (alert,) = mon.take(T0 + timedelta(minutes=1), "confirm")["alerts"]
    assert alert["type"] == "returned_without_scan" and alert["spot_id"] == B and alert["last_person_id"] == "P-1"


def test_checked_out_tool_with_empty_spot_is_normal(conn, mon, cam, clock):
    k = Kiosk(conn, "gate-1", clock=clock)
    k.start("ID-1001", "out"); k.scan("T-0001"); k.finish()
    cam.picture = shelf(B)
    mon.take(T0, "scheduled")
    assert mon.take(T0 + timedelta(hours=1), "scheduled")["alerts"] == [] and mon.confirm_at is None


def test_broken_camera_becomes_check_camera_alerts(conn, mon, cam):
    cam.broken = True
    r = mon.take(T0, "scheduled")
    assert r["spots"] == {A: "unclear", B: "unclear"} and r["alerts"] == []
    alerts = mon.take(T0 + timedelta(minutes=1), "confirm")["alerts"]
    assert {(a["spot_id"], a["type"]) for a in alerts} == {(A, "check_camera"), (B, "check_camera")}
    assert all(a["snapshot_photo"] is None for a in alerts)
    assert queued(conn, "photo") == []


def test_snapshot_is_queued_for_the_sheet(conn, mon, cam):
    cam.picture = shelf(A)
    mon.take(T0, "scheduled")
    (photo,), (snap,) = queued(conn, "photo"), queued(conn, "snapshot")
    assert snap["photo"] == photo["path"] and snap["reason"] == "scheduled"
    assert (snap["full"], snap["empty"], snap["unclear"]) == (1, 1, 0)
    assert json.loads(snap["spots"]) == {A: "full", B: "empty"}
    assert sync.pending(conn) == 2


def test_uncalibrated_spots_are_skipped(conn, mon):
    conn.execute("UPDATE spots SET box = NULL WHERE spot_id = ?", (A,))
    conn.execute("UPDATE spots SET box = 'left side' WHERE spot_id = ?", (B,))
    assert mon.take(T0, "scheduled")["spots"] == {}
