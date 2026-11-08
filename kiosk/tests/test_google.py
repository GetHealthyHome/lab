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

    def _tab(self, url):
        rng = unquote(url.split("/values/")[1]).removesuffix(":append")
        return rng.split("!")[0], rng

    def get(self, url, params=None):
        if self.down:
            raise ConnectionError("offline")
        tab, rng = self._tab(url)
        rows = self.tabs.get(tab, [])
        return Resp({"values": rows[:1] if rng.endswith("1:1") else rows})

    def put(self, url, params=None, json=None):
        tab, _ = self._tab(url)
        rows = self.tabs.setdefault(tab, [[]])
        rows[0] = json["values"][0]
        return Resp({})

    def post(self, url, params=None, json=None, files=None):
        if self.down:
            raise ConnectionError("offline")
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
    assert GoogleSink(g, conn, "S", "F").pull() == {"Spots": 1, "Tools": 2, "People": 2}

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
