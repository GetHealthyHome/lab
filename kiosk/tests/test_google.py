"""GoogleSink against a fake Google: a dict of tabs and a dict of Drive files. No credentials needed."""
import json
from urllib.parse import unquote

import pytest

from lab_toolbox import sync
from lab_toolbox.google import GoogleError, GoogleSink
from lab_toolbox.sessions import Kiosk


class Resp:
    def __init__(self, body=None, status=200):
        self.status_code, self._body = status, body or {}
        self.content = json.dumps(self._body).encode()
        self.text = self.content.decode()

    def json(self):
        return self._body


class FakeGoogle:
    def __init__(self, tabs=None):
        self.tabs = tabs or {}
        self.files = {}
        self.down = False
        self.strict = False  # True: reading a tab that doesn't exist fails, as the real Sheets API does

    def _tab(self, url):
        rng = unquote(url.split("/values/")[1]).removesuffix(":append")
        return rng.split("!")[0], rng

    def get(self, url, params=None):
        if self.down:
            raise ConnectionError("offline")
        tab, rng = self._tab(url)
        if self.strict and tab not in self.tabs:
            return Resp({"error": {"code": 400, "message": f"Unable to parse range: {rng}"}}, 400)
        rows = self.tabs.get(tab, [])
        return Resp({"values": rows[:1] if rng.endswith("1:1") else rows})

    def put(self, url, params=None, json=None):
        tab, rng = self._tab(url)
        rows = self.tabs.setdefault(tab, [[]])
        cell = rng.split("!")[1]
        i = 0 if cell == "1:1" else int(cell.lstrip("A")) - 1
        rows[i] = json["values"][0]
        return Resp({})

    def post(self, url, params=None, json=None, files=None):
        if self.down:
            raise ConnectionError("offline")
        if "requests" in (json or {}):
            self.tabs[json["requests"][0]["addSheet"]["properties"]["title"]] = [[]]
            return Resp({})
        if url.endswith(":batchUpdate"):
            for d in json["data"]:
                tab, cell = unquote(d["range"]).split("!")
                col, row = "".join(ch for ch in cell if ch.isalpha()), int("".join(ch for ch in cell if ch.isdigit()))
                c = sum((ord(ch) - 64) * 26 ** i for i, ch in enumerate(reversed(col))) - 1
                r = self.tabs[tab][row - 1]
                r.extend([""] * (c + 1 - len(r)))
                r[c] = d["values"][0][0]
            return Resp({})
        if files:
            meta = __import__("json").loads(files["metadata"][1])
            fid = f"F{len(self.files) + 1}"
            self.files[fid] = (meta, files["file"][1].read())
            return Resp({"id": fid, "webViewLink": f"https://drive.google.com/file/d/{fid}/view"})
        tab, _ = self._tab(url)
        self.tabs.setdefault(tab, []).extend(json["values"])
        return Resp({"updates": {"updatedRows": 1}})


class Cam:
    def __init__(self, path):
        self.path = path

    def capture(self):
        self.path.write_bytes(b"jpg")
        return str(self.path)


TXN_HEADER = ["txn_id", "ts", "asset_id", "person_id", "action", "session_id", "gate_photo", "override_by", "kiosk_id"]


def test_checkout_lands_in_sheet_with_drive_link(conn, tmp_path, clock):
    g = FakeGoogle({"Transactions": [list(reversed(TXN_HEADER))]})  # staff reordered columns: still matched by name
    sink = GoogleSink(g, conn, "SHEET", "FOLDER")
    k = Kiosk(conn, "gate-1", camera=Cam(tmp_path / "s1.jpg"), clock=clock)
    k.start("ID-1001", "out")
    k.scan("T-0001")
    k.scan("T-0002")
    k.finish()

    assert sync.sync_once(conn, sink) == 3
    assert sync.pending(conn) == 0
    (meta, data), = g.files.values()
    assert meta == {"name": "s1.jpg", "parents": ["FOLDER"]} and data == b"jpg"
    assert not (tmp_path / "s1.jpg").exists()

    header, *rows = g.tabs["Transactions"]
    got = [dict(zip(header, r)) for r in rows]
    assert [r["asset_id"] for r in got] == ["HHA-0001", "HHA-0002"]
    assert {r["gate_photo"] for r in got} == {"https://drive.google.com/file/d/F1/view"}
    assert got[0]["override_by"] == "" and got[0]["kiosk_id"] == "gate-1"


def test_empty_tab_gets_header_and_new_fields_add_columns(conn):
    g = FakeGoogle()
    sink = GoogleSink(g, conn, "S", "F")
    sink.send("transaction", {"txn_id": "a", "asset_id": "HHA-0001"})
    sink.send("transaction", {"txn_id": "b", "asset_id": "HHA-0002", "due_at": "later"})
    assert g.tabs["Transactions"] == [["txn_id", "asset_id", "due_at"], ["a", "HHA-0001"], ["b", "HHA-0002", "later"]]


def test_offline_keeps_queue_and_retry_does_not_reupload(conn, tmp_path):
    photo = tmp_path / "p.jpg"
    photo.write_bytes(b"x")
    g = FakeGoogle({"Transactions": [["txn_id", "gate_photo"]]})
    sink = GoogleSink(g, conn, "S", "F")
    sync.enqueue(conn, "photo", {"path": str(photo)})
    sync.enqueue(conn, "transaction", {"txn_id": "a", "gate_photo": str(photo)})

    g.down = True
    assert sync.sync_once(conn, sink) == 0 and sync.pending(conn) == 2

    # Power cut after Drive took the photo but before the outbox marked it sent.
    g.down = False
    sink.send("photo", {"path": str(photo)})
    assert sync.sync_once(conn, sink) == 2
    assert len(g.files) == 1
    assert g.tabs["Transactions"][1] == ["a", "https://drive.google.com/file/d/F1/view"]


def test_unknown_kind_and_http_errors_stay_queued(conn):
    class Refusing(FakeGoogle):
        def post(self, *a, **kw):
            return Resp({"error": "quota"}, status=429)

    sink = GoogleSink(Refusing({"Transactions": [["txn_id"]]}), conn, "S", "F")
    with pytest.raises(GoogleError, match="no Sheet tab"):
        sink.send("mystery", {})
    sync.enqueue(conn, "transaction", {"txn_id": "a"})
    assert sync.sync_once(conn, sink) == 0
    assert "HTTP 429" in conn.execute("SELECT last_error FROM sync_queue").fetchone()[0]


def test_pull_updates_lists_from_sheet(conn):
    g = FakeGoogle({
        "Spots": [["spot_id", "label"], ["LOC-S02-B01-H01", "Shelf 02, bay 01, hook 01"], ["", ""]],
        "Tools": [
            ["asset_id", "qr_code", "name", "category", "spot_id", "status"],
            ["HHA-0001", "T-0001", "Fluke multimeter", "Electrical", "LOC-S03-B02-H04", "out"],
            ["HHA-0003", "T-0003", "Torque wrench", "", "LOC-S02-B01-H01", "quarantined"],
        ],
        "People": [
            ["person_id", "id_code", "name", "role", "active"],
            ["P-2", "ID-1002", "Sam", "Student", False],
            ["P-3", "ID-1003", "Ms. Rivera", "instructor", True],
        ],
    })
    assert GoogleSink(g, conn, "S", "F").pull() == {"Spots": 1, "Tools": 2, "People": 2, "Settings": 0}

    tools = {r["asset_id"]: dict(r) for r in conn.execute("SELECT * FROM tools")}
    assert tools["HHA-0001"]["name"] == "Fluke multimeter" and tools["HHA-0001"]["status"] == "available"
    assert tools["HHA-0003"]["status"] == "quarantined" and tools["HHA-0003"]["category"] is None
    people = {r["person_id"]: dict(r) for r in conn.execute("SELECT * FROM people")}
    assert people["P-2"]["active"] == 0
    assert people["P-3"]["role"] == "instructor" and people["P-1"]["name"] == "Jordan"  # not in Sheet: left alone


def test_bad_pull_changes_nothing(conn):
    g = FakeGoogle({
        "Spots": [["spot_id", "label"]],
        "Tools": [["asset_id", "qr_code", "name", "spot_id"], ["HHA-0001", "T-0001", "Renamed", "LOC-NOPE"]],
        "People": [["person_id"]],
    })
    with pytest.raises(Exception):
        GoogleSink(g, conn, "S", "F").pull()
    assert conn.execute("SELECT name FROM tools WHERE asset_id = 'HHA-0001'").fetchone()[0] == "Digital multimeter"


def test_alert_update_rewrites_its_row_but_keeps_dashboard_answer(conn):
    g = FakeGoogle({"Alerts": [["alert_id", "type", "status", "resolution_note"]]})
    sink = GoogleSink(g, conn, "S", "F")
    sink.send("alert", {"alert_id": "A1", "type": "check_camera", "status": "open"})
    g.tabs["Alerts"][1][2] = "acknowledged"  # instructor clicked Acknowledge on the dashboard
    sink.send("alert", {"alert_id": "A1", "type": "missing_tool", "status": "open", "last_seen": "t2"})
    assert g.tabs["Alerts"] == [["alert_id", "type", "status", "resolution_note", "last_seen"],
                                ["A1", "missing_tool", "acknowledged", "", "t2"]]


def test_pull_brings_settings_and_alert_answers(conn):
    conn.execute("INSERT INTO alerts (alert_id, opened, type) VALUES ('A1', 't', 'missing_tool')")
    g = FakeGoogle({
        "Spots": [["spot_id"]], "Tools": [["asset_id"]], "People": [["person_id"]],
        "Settings": [["key", "value"], ["snapshot_interval_hours", 3]],
        "Alerts": [["alert_id", "status", "resolution_note"], ["A1", "False alarm", "my hand"], ["GONE", "resolved", ""]],
    })
    assert GoogleSink(g, conn, "S", "F").pull()["Settings"] == 1
    assert conn.execute("SELECT value FROM settings WHERE key = 'snapshot_interval_hours'").fetchone()[0] == "3"
    assert tuple(conn.execute("SELECT status, resolution_note FROM alerts").fetchone()) == ("false_alarm", "my hand")


def test_health_row_is_one_per_kiosk(conn):
    g = FakeGoogle()
    sink = GoogleSink(g, conn, "S", "F")
    sync.enqueue(conn, "transaction", {"txn_id": "a"})
    sink.report_health("gate-1")
    sink.report_health("gate-1")
    header, *rows = g.tabs["Health"]
    assert header == ["kiosk_id", "last_sync", "queue_size", "last_snapshot"] and len(rows) == 1
    assert rows[0][0] == "gate-1" and rows[0][2] == 1


def test_pull_takes_dashboard_returns_only(conn, clock):
    k = Kiosk(conn, "gate-1", clock=clock)
    k.start("ID-1001", "out")
    k.scan("T-0001")
    k.finish()
    g = FakeGoogle({
        "Spots": [["spot_id"]], "Tools": [["asset_id"]], "People": [["person_id"]],
        "Transactions": [["txn_id", "ts", "asset_id", "person_id", "action", "session_id", "override_by", "kiosk_id"],
                         ["D1", "2026-11-08T03:00:00+00:00", "HHA-0001", "P-1", "in", "dash-1", "boss@x.org", "dashboard"],
                         ["D2", "2026-11-08T03:00:00+00:00", "NOPE", "P-1", "in", "dash-2", "", "dashboard"],
                         ["G1", "2026-11-08T03:00:00+00:00", "HHA-0002", "P-1", "out", "s", "", "gate-1"]],
    })
    sink = GoogleSink(g, conn, "S", "F")
    sink.pull()
    sink.pull()  # twice: no duplicate
    rows = conn.execute("SELECT txn_id, override_by FROM transactions WHERE kiosk_id = 'dashboard'").fetchall()
    assert [tuple(r) for r in rows] == [("D1", None)]  # unknown tool skipped; an email isn't a person id
    assert conn.execute("SELECT state FROM tool_state WHERE asset_id = 'HHA-0001'").fetchone()[0] == "available"


def test_pull_waits_until_our_changes_are_sent(conn):
    from lab_toolbox.sync_service import run_once

    g = FakeGoogle({"Spots": [["spot_id"]], "Tools": [["asset_id"]], "People": [["person_id"]]})
    sink = GoogleSink(g, conn, "S", "F")
    sync.enqueue(conn, "mystery", {})  # can't be sent
    assert run_once(conn, sink, do_pull=True) is False
    conn.execute("DELETE FROM sync_queue")
    assert run_once(conn, sink, do_pull=True) is True


def test_pull_brings_batteries_and_kits(conn):
    g = FakeGoogle({
        "Spots": [["spot_id"]], "People": [["person_id", "id_code", "name", "email"], ["P-1", "ID-1001", "Jordan", "j@x.org"]],
        "Tools": [["asset_id", "qr_code", "name", "needs_battery", "battery_group_id", "is_battery"],
                  ["HHA-0001", "T-0001", "Drill", "yes", "DW20", ""], ["HHA-0002", "T-0002", "Battery", "", "DW20", True]],
        "BatteryGroups": [["group_id", "name"], ["DW20", "DeWalt 20V Max"]],
        "Kits": [["kit_id", "case_code", "name", "check_mode", "status"], ["K1", "CASE-1", "Kit", "Verified", "out"]],
        "KitItems": [["kit_id", "asset_id"], ["K1", "HHA-0001"], ["K1", "TYPO"], ["NOKIT", "HHA-0002"]],
    })
    sink = GoogleSink(g, conn, "S", "F")
    sink.pull()
    t = {r["asset_id"]: (r["needs_battery"], r["is_battery"], r["battery_group_id"]) for r in conn.execute("SELECT * FROM tools")}
    assert t == {"HHA-0001": (1, 0, "DW20"), "HHA-0002": (0, 1, "DW20")}
    assert tuple(conn.execute("SELECT check_mode, status FROM kits").fetchone()) == ("verified", "available")
    assert [tuple(r) for r in conn.execute("SELECT * FROM kit_items")] == [("K1", "HHA-0001")]  # bad rows skipped
    assert conn.execute("SELECT email FROM people WHERE person_id = 'P-1'").fetchone()[0] == "j@x.org"
    g.tabs["KitItems"] = [["kit_id", "asset_id"]]  # dashboard removed the piece
    sink.pull()
    assert conn.execute("SELECT COUNT(*) FROM kit_items").fetchone()[0] == 0


def test_kiosk_pin_change_writes_only_that_cell(conn):
    g = FakeGoogle({"People": [["person_id", "name", "pin_hash", "email"], ["P-1", "Jordan", "", "j@x.org"]]})
    sink = GoogleSink(g, conn, "S", "F")
    sink.send("person", {"person_id": "P-1", "pin_hash": "$argon2id$abc"})
    assert g.tabs["People"][1] == ["P-1", "Jordan", "$argon2id$abc", "j@x.org"]


def test_tool_photo_stays_on_pi_and_survives_a_pull(conn, tmp_path, clock):
    conn.execute("INSERT INTO people (person_id, id_code, name, role) VALUES ('P-9', 'ID-9', 'Ms. Rivera', 'instructor')")
    k = Kiosk(conn, "gate-1", camera=Cam(tmp_path / "drill.jpg"), clock=clock)
    k.next_action = "instructor"
    k.handle("ID-9")
    k.enter_pin("9999")
    assert k.take_tool_photo("HHA-0001")["name"] == "Digital multimeter"

    g = FakeGoogle({"Tools": [["asset_id", "qr_code", "name", "spot_id"],
                              ["HHA-0001", "T-0001", "Digital multimeter", "LOC-S03-B02-H04"]]})
    sink = GoogleSink(g, conn, "S", "F")
    sync.sync_once(conn, sink)
    assert (tmp_path / "drill.jpg").exists()  # the screen still needs it
    header, row = g.tabs["Tools"]
    assert dict(zip(header, row))["photo"] == "https://drive.google.com/file/d/F1/view"

    g.tabs.update(Spots=[["spot_id", "label"]], People=[["person_id"]])
    sink.pull()
    assert conn.execute("SELECT photo FROM tools WHERE asset_id = 'HHA-0001'").fetchone()[0] == str(tmp_path / "drill.jpg")


def test_missing_tab_is_created(conn):
    google = FakeGoogle({"People": [["person_id"]]})
    google.strict = True
    GoogleSink(google, conn, "SHEET", "FOLDER").report_health("gate-1")
    header, row = google.tabs["Health"]
    assert header[0] == "kiosk_id" and row[0] == "gate-1"


def test_photos_go_up_under_the_drive_sign_in_when_there_is_one(conn, tmp_path):
    sheet, drive = FakeGoogle(), FakeGoogle()
    sink = GoogleSink(sheet, conn, "S", "F", drive=drive)
    (tmp_path / "p.jpg").write_bytes(b"jpg")
    sink.send("photo", {"path": str(tmp_path / "p.jpg")})
    assert len(drive.files) == 1 and not sheet.files


def test_drive_login_waits_for_approval_then_makes_the_folder():
    from lab_toolbox import drive_login

    class Http:
        def __init__(self):
            self.answers = [
                Resp({"device_code": "D", "user_code": "ABCD-EFGH", "verification_url": "https://www.google.com/device",
                      "expires_in": 1800, "interval": 5}),
                Resp({"error": "authorization_pending"}, 428),
                Resp({"error": "slow_down"}, 403),
                Resp({"access_token": "AT", "refresh_token": "RT"}),
                Resp({"id": "FOLDER"}),
            ]
            self.calls = []

        def post(self, url, **kw):
            self.calls.append((url, kw))
            return self.answers.pop(0)

    http, said, waits = Http(), [], []
    info = drive_login.login({"installed": {"client_id": "C", "client_secret": "S"}}, http, sleep=waits.append, say=said.append)
    assert info == {"type": "authorized_user", "client_id": "C", "client_secret": "S", "refresh_token": "RT", "folder_id": "FOLDER"}
    assert "ABCD-EFGH" in said[0] and waits == [5, 5, 10]  # slow_down: back off
    assert http.calls[-1][1]["headers"] == {"Authorization": "Bearer AT"}
    assert http.calls[-1][1]["json"]["mimeType"] == "application/vnd.google-apps.folder"

    http = Http()
    http.answers[1] = Resp({"error": "access_denied"}, 403)
    with pytest.raises(SystemExit, match="access_denied"):
        drive_login.login({"client_id": "C", "client_secret": "S"}, http, sleep=waits.append, say=said.append)


def test_pull_survives_a_mistyped_role(conn):
    g = FakeGoogle({
        "Spots": [["spot_id"]], "Tools": [["asset_id"]],
        "People": [["person_id", "id_code", "name", "role", "active"], ["P-7", "ID-7", "Pat", "Admin", ""],
                   ["P-8", "ID-8", "Lee", " Instructor ", "no"]],
    })
    assert GoogleSink(g, conn, "S", "F").pull()["People"] == 2
    got = {r["person_id"]: (r["role"], r["active"]) for r in conn.execute("SELECT * FROM people WHERE person_id IN ('P-7', 'P-8')")}
    # unknown role: least access, and the rest still syncs. Blank active: active, as the dashboard reads it.
    assert got == {"P-7": ("student", 1), "P-8": ("instructor", 0)}
