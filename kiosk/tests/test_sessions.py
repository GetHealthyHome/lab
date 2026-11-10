import pytest

from lab_toolbox.sessions import Kiosk, Rejected


class FakeCamera:
    def __init__(self, path="/photos/a.jpg", fail=False):
        self.path, self.fail, self.calls = path, fail, 0

    def capture(self):
        self.calls += 1
        if self.fail:
            raise OSError("camera unplugged")
        return self.path


@pytest.fixture
def kiosk(conn, clock):
    return Kiosk(conn, "gate-1", camera=FakeCamera(), clock=clock)


def checkout(kiosk, id_code, *codes):
    kiosk.start(id_code, "out")
    for c in codes:
        kiosk.scan(c)
    return kiosk.finish()


def test_checkout_several_tools(kiosk, conn):
    result = checkout(kiosk, "ID-1001", "T-0001", "T-0002")
    assert [i["asset_id"] for i in result["items"]] == ["HHA-0001", "HHA-0002"]
    rows = conn.execute("SELECT * FROM transactions").fetchall()
    assert len(rows) == 2  # K2, K3
    assert {r["session_id"] for r in rows} == {result["session_id"]}
    assert {r["gate_photo"] for r in rows} == {"/photos/a.jpg"}
    assert kiosk.camera.calls == 2  # one per tool tapped out
    assert {r["kiosk_id"] for r in rows} == {"gate-1"}
    kinds = [r[0] for r in conn.execute("SELECT kind FROM sync_queue ORDER BY id")]
    assert kinds == ["photo", "transaction", "transaction"]  # same file twice is queued once  # K10: queued with the records


def test_return_shows_spot(kiosk):
    checkout(kiosk, "ID-1001", "T-0001")
    kiosk.start("ID-1001", "in")
    kiosk.scan("T-0001")
    result = kiosk.finish()
    assert result["items"][0]["return_to"] == "Shelf 03, bay 02, hook 04"  # K5


@pytest.mark.parametrize(
    "setup, code, reason",
    [
        (None, "NOPE", "Unknown code"),
        ("UPDATE tools SET status = 'retired' WHERE asset_id = 'HHA-0001'", "T-0001", "retired"),
        ("UPDATE tools SET status = 'quarantined' WHERE asset_id = 'HHA-0001'", "T-0001", "inspect"),
    ],
)
def test_checkout_rejections(kiosk, conn, setup, code, reason):  # K6
    if setup:
        conn.execute(setup)
    kiosk.start("ID-1001", "out")
    with pytest.raises(Rejected, match=reason):
        kiosk.scan(code)


def test_reject_tool_out_with_someone_else(kiosk):
    checkout(kiosk, "ID-1002", "T-0001")
    kiosk.start("ID-1001", "out")
    with pytest.raises(Rejected, match="someone else"):
        kiosk.scan("T-0001")


def test_reject_own_tool_and_duplicate_scan(kiosk):
    checkout(kiosk, "ID-1001", "T-0001")
    kiosk.start("ID-1001", "out")
    with pytest.raises(Rejected, match="You already have"):
        kiosk.scan("T-0001")
    kiosk.scan("T-0002")
    with pytest.raises(Rejected, match="already in your list"):
        kiosk.scan("T-0002")


def test_return_of_tool_not_out(kiosk):
    kiosk.start("ID-1001", "in")
    with pytest.raises(Rejected, match="isn't checked out"):
        kiosk.scan("T-0001")


def test_unknown_or_inactive_id(kiosk, conn):
    with pytest.raises(Rejected, match="ID not recognized"):
        kiosk.start("ID-9999", "out")
    conn.execute("UPDATE people SET active = 0 WHERE id_code = 'ID-1001'")
    with pytest.raises(Rejected, match="ID not recognized"):
        kiosk.start("ID-1001", "out")


def test_one_session_at_a_time(kiosk):
    kiosk.start("ID-1001", "out")
    with pytest.raises(Rejected, match="Finish the current session"):
        kiosk.start("ID-1002", "out")


def test_scan_without_session(kiosk):
    with pytest.raises(Rejected, match="Enter your PIN"):
        kiosk.scan("T-0001")
    with pytest.raises(Rejected, match="Enter your PIN"):
        kiosk.handle("T-0001")


def test_each_tool_tapped_out_gets_its_own_photo(conn, clock):
    shots = iter(["/p/1.jpg", "/p/2.jpg", "/p/done.jpg"])

    class Cam:
        def capture(self):
            return next(shots)

    kiosk = Kiosk(conn, "gate-1", camera=Cam(), clock=clock)
    conn.execute("UPDATE tools SET nfc_uid = '04A1B2C3' WHERE asset_id = 'HHA-0001'")
    kiosk.start("ID-1001", "out")
    assert kiosk.handle("04A1B2C3")["asset_id"] == "HHA-0001"  # NFC sticker
    kiosk.handle("T-0002")  # printed label (the QR fallback)
    result = kiosk.finish()
    rows = conn.execute("SELECT asset_id, gate_photo FROM transactions ORDER BY asset_id").fetchall()
    assert [tuple(r) for r in rows] == [("HHA-0001", "/p/1.jpg"), ("HHA-0002", "/p/2.jpg")]
    assert result["photo"] == "/p/1.jpg"  # no extra photo at Done when every tool has one
    assert [r[0] for r in conn.execute("SELECT kind FROM sync_queue ORDER BY id")] == ["photo", "photo", "transaction", "transaction"]


def test_idle_timeout_saves_and_scans_reset(kiosk, conn, clock):  # K9
    kiosk.start("ID-1001", "out")
    clock.t += 40
    kiosk.scan("T-0001")  # resets the countdown
    clock.t += 40
    assert kiosk.seconds_left() == 5
    assert kiosk.expire_idle() is None
    clock.t += 5
    saved = kiosk.expire_idle()
    assert saved["items"][0]["asset_id"] == "HHA-0001"
    assert kiosk.session is None
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1


def test_empty_session_times_out_without_records(kiosk, conn, clock):
    kiosk.start("ID-1001", "out")
    clock.t += 45
    assert kiosk.expire_idle()["items"] == []
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    assert kiosk.camera.calls == 0


def test_camera_failure_does_not_block_checkout(conn, clock):
    kiosk = Kiosk(conn, "gate-1", camera=FakeCamera(fail=True), clock=clock)
    result = checkout(kiosk, "ID-1001", "T-0001")
    assert result["photo"] is None
    assert kiosk.camera.calls == 2  # the tap's photo failed, so Done tried once more
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
