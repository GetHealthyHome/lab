"""K7, K8, K11-K17: PINs, instructor mode, batteries, kits, damage, worn labels, loans, badge enroll."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from lab_toolbox import loans
from lab_toolbox.app import create_app
from lab_toolbox.sessions import Kiosk, Locked, Rejected, WrongBattery

NOW = datetime(2026, 11, 9, 18, 0, tzinfo=timezone.utc)

EXTRA = """
INSERT INTO people (person_id, id_code, name, role) VALUES ('P-9', 'ID-9', 'Ms. Rivera', 'instructor');
INSERT INTO battery_groups VALUES ('DW20', 'DeWalt 20V Max'), ('MK18', 'Makita 18V');
INSERT INTO spots VALUES ('LOC-CHG01', 'Charger 01', NULL, NULL);
INSERT INTO tools (asset_id, qr_code, name, needs_battery, battery_group_id) VALUES ('HHA-0010', 'T-0010', 'DeWalt drill', 1, 'DW20');
INSERT INTO tools (asset_id, qr_code, name, is_battery, battery_group_id, spot_id) VALUES
  ('HHA-B1', 'B-1', 'DeWalt 20V battery 1', 1, 'DW20', 'LOC-CHG01'),
  ('HHA-B2', 'B-2', 'Makita 18V battery', 1, 'MK18', 'LOC-CHG01');
INSERT INTO tools (asset_id, qr_code, name) VALUES ('HHA-K1', 'T-K1', 'Crimper'), ('HHA-K2', 'T-K2', 'Stripper');
INSERT INTO kits (kit_id, case_code, name, check_mode) VALUES ('KIT-1', 'CASE-1', 'Crimp kit', 'standard'),
  ('KIT-2', 'CASE-2', 'Checked kit', 'verified');
INSERT INTO kit_items VALUES ('KIT-1', 'HHA-K1'), ('KIT-1', 'HHA-K2');
"""


class Wall:
    def __init__(self):
        self.t = NOW

    def __call__(self):
        return self.t


@pytest.fixture
def wall():
    return Wall()


@pytest.fixture
def k(conn, clock, wall):
    conn.executescript(EXTRA)
    return Kiosk(conn, "gate-1", clock=clock, wall=wall)


def queued(conn, kind):
    return [json.loads(r[0]) for r in conn.execute("SELECT payload FROM sync_queue WHERE kind = ? ORDER BY id", (kind,))]


def pins_on(conn):
    conn.execute("UPDATE settings SET value = 'yes' WHERE key = 'require_pin'")


# -- K11 PINs ----------------------------------------------------------------------------------------

def test_first_scan_sets_a_pin_then_it_is_checked(k, conn):
    pins_on(conn)
    assert k.handle("ID-1001")["pin"] == "setup"
    with pytest.raises(Rejected, match="Choose your 4-digit PIN"):
        k.scan("T-0001")
    with pytest.raises(Rejected, match="4 digits"):
        k.enter_pin("12a4")
    k.enter_pin("2468")
    k.scan("T-0001")
    k.finish()
    h = conn.execute("SELECT pin_hash FROM people WHERE person_id = 'P-1'").fetchone()[0]
    assert h.startswith("$argon2id$") and "2468" not in h
    assert queued(conn, "person") == [{"person_id": "P-1", "pin_hash": h}]  # only the PIN cell goes to the Sheet

    assert k.handle("ID-1001")["pin"] == "check"
    with pytest.raises(Rejected, match="Wrong PIN. 4 tries left"):
        k.enter_pin("1111")
    assert k.enter_pin("2468")["pin"] == "ok"


def test_five_wrong_pins_lock_the_id_for_15_minutes(k, conn, wall):
    pins_on(conn)
    k.handle("ID-1001")
    k.enter_pin("2468")
    k.cancel()
    k.handle("ID-1001")
    for _ in range(4):
        with pytest.raises(Rejected):
            k.enter_pin("0000")
    with pytest.raises(Locked) as e:
        k.enter_pin("0000")
    assert e.value.sound == "locked" and e.value.details["until"] == "2026-11-09T18:15:00+00:00"
    assert k.session is None
    alert, = queued(conn, "alert")
    assert alert["type"] == "id_locked" and alert["last_person_name"] == "Jordan"
    with pytest.raises(Locked):
        k.start("ID-1001", "out")
    wall.t += timedelta(minutes=16)
    k.start("ID-1001", "out")
    assert k.enter_pin("2468")["pin"] == "ok"


# -- K7 instructor mode, K17 badge enroll ---------------------------------------------------------------

def login(k, conn, pin="9999"):
    out = k.handle("ID-9") if k.session else (setattr(k, "next_action", "instructor") or k.handle("ID-9"))
    assert "instructor_pin" in out
    k.enter_pin(pin)
    assert k.instructor["name"] == "Ms. Rivera"


def test_instructor_types_asset_number_for_a_worn_label(k, conn):
    k.handle("ID-1001")
    with pytest.raises(Rejected, match="Instructor: scan your badge"):
        k.add_by_asset("HHA-0002")
    login(k, conn)  # badge scanned in the middle of Jordan's session
    k.add_by_asset("HHA-0002")
    k.finish()
    t = conn.execute("SELECT person_id, override_by FROM transactions").fetchone()
    assert tuple(t) == ("P-1", "P-9")
    a = conn.execute("SELECT who, reason FROM audit_log").fetchone()
    assert a["who"] == "Ms. Rivera" and "entered by hand" in a["reason"]
    assert queued(conn, "audit")[0]["who"] == "Ms. Rivera"


def test_instructor_starts_session_for_id_that_wont_scan_and_bypasses_hold(k, conn, wall):
    login(k, conn)
    k.start_for("P-2", "out")
    k.scan("T-0001")
    k.finish()
    wall.t += timedelta(days=1)  # Sam's multimeter is now overdue
    with pytest.raises(Rejected, match="Return your overdue tools first: Digital multimeter"):
        k.start("ID-1002", "out")
    k.start_for("P-2", "out")  # an instructor can still let them borrow
    assert k.session.override_by["name"] == "Ms. Rivera"


def test_badge_enroll_links_next_scanned_card(k, conn):
    login(k, conn)
    assert k.enroll("P-2")["waiting"]
    with pytest.raises(Rejected, match="belongs to Jordan"):
        k.handle("ID-1001")
    k.enroll("P-2")
    assert k.handle("NEW-CARD-7") == {"enroll": "Sam", "id_code": "NEW-CARD-7"}
    assert conn.execute("SELECT id_code FROM people WHERE person_id = 'P-2'").fetchone()[0] == "NEW-CARD-7"
    assert queued(conn, "person")[-1] == {"person_id": "P-2", "id_code": "NEW-CARD-7"}


def test_badge_scan_waits_for_the_pin(k, conn, clock):
    clock.t += 1000  # a kiosk that has been up a while: the badge must not count as already idle
    k.next_action = "instructor"
    k.handle("ID-9")
    k.expire_idle()
    assert k.state()["instructor_pin"]


def test_instructor_logs_out_after_walking_away(k, conn, clock):
    login(k, conn)
    clock.t += 200
    k.expire_idle()
    assert k.instructor is None


# -- K12 batteries -----------------------------------------------------------------------------------

def test_cordless_tool_needs_a_matching_battery(k, conn):
    k.start("ID-1001", "out")
    r = k.scan("T-0010")
    assert r["battery_needed"]["group"] == "DeWalt 20V Max"
    assert [b["spot"] for b in r["battery_needed"]["available"]] == ["Charger 01"]
    with pytest.raises(Rejected, match="Scan a battery for DeWalt drill"):
        k.finish()
    with pytest.raises(WrongBattery, match="Scan a DeWalt 20V Max battery") as e:
        k.scan("B-2")
    assert e.value.details == {"required": "DeWalt 20V Max", "scanned": "Makita 18V"}
    assert k.scan("B-1")["paired_to"] == "DeWalt drill"
    k.finish()
    rows = {r["asset_id"]: r["paired_battery_id"] for r in conn.execute("SELECT * FROM transactions")}
    assert rows == {"HHA-0010": "HHA-B1", "HHA-B1": None}


def test_removing_the_drill_lets_the_rest_go(k):
    k.start("ID-1001", "out")
    k.scan("T-0010")
    k.scan("T-0001")
    k.remove("HHA-0010")
    assert [i["asset_id"] for i in k.finish()["items"]] == ["HHA-0001"]


def test_timeout_saves_an_unpaired_drill_anyway(k, conn, clock):
    k.start("ID-1001", "out")
    k.scan("T-0010")
    clock.t += 45
    assert k.expire_idle()["items"][0]["asset_id"] == "HHA-0010"  # it left the cage either way


# -- K13 kits --------------------------------------------------------------------------------------------

def test_standard_kit_checks_out_every_piece_and_short_return_opens_alert(k, conn):
    k.start("ID-1001", "out")
    r = k.scan("CASE-1")
    assert [p["name"] for p in r["pieces"]] == ["Crimper", "Stripper"]
    saved = k.finish()
    assert {i["kit_id"] for i in saved["items"]} == {"KIT-1"}

    k.start("ID-1001", "in")
    assert "Confirm" in k.scan("CASE-1")["confirm"]
    with pytest.raises(Rejected, match="Confirm the pieces of Crimp kit"):
        k.finish()
    assert k.confirm_kit("KIT-1", missing=["HHA-K2"])["missing"] == ["HHA-K2"]
    saved = k.finish()
    assert [i["asset_id"] for i in saved["items"]] == ["HHA-K1"]  # partial return
    assert saved["kits_short"] == [{"kit_id": "KIT-1", "name": "Crimp kit", "missing": ["Stripper"]}]
    assert conn.execute("SELECT status FROM kits WHERE kit_id = 'KIT-1'").fetchone()[0] == "incomplete"
    alert = queued(conn, "alert")[-1]
    assert alert["type"] == "kit_incomplete" and "Stripper" in alert["details"] and alert["last_person_name"] == "Jordan"
    assert conn.execute("SELECT state FROM tool_state WHERE asset_id = 'HHA-K2'").fetchone()[0] == "out"


def test_verified_kit_needs_each_piece_scanned(k, conn):
    conn.execute("INSERT INTO kit_items VALUES ('KIT-2', 'HHA-0001'), ('KIT-2', 'HHA-0002')")
    k.start("ID-1001", "out")
    k.scan("CASE-2")
    k.scan("T-0001")
    with pytest.raises(Rejected, match="Manifold gauge set still to scan"):
        k.finish()
    k.scan("T-0002")
    assert len(k.finish()["items"]) == 2


def test_kit_skips_pieces_already_out(k):
    k.start("ID-1002", "out")
    k.scan("T-K2")
    k.finish()
    k.start("ID-1001", "out")
    assert k.scan("CASE-1")["not_in_kit"] == ["Stripper"]


# -- K8, K14, K15 reports ---------------------------------------------------------------------------------

class Cam:
    def capture(self):
        return "/photos/closeup.jpg"


def test_safety_hazard_quarantines_until_an_instructor_clears_it(k, conn):
    k.camera = Cam()
    k.start("ID-1001", "out")
    k.scan("T-0001")
    k.finish()
    k.start("ID-1001", "in")
    r = k.report_damage("T-0001", "safety_hazard", ["Cracked case"], "cord frayed")
    assert r["quarantined"]
    k.scan("T-0001")  # still returns while quarantined
    k.finish()
    rep, = queued(conn, "damage")
    assert rep["reported_by"] == "P-1" and rep["photo"] == "/photos/closeup.jpg" and rep["tags"] == "Cracked case"
    assert queued(conn, "tool") == [{"asset_id": "HHA-0001", "status": "quarantined"}]
    assert queued(conn, "alert")[-1]["type"] == "damage"
    k.start("ID-1002", "out")
    with pytest.raises(Rejected, match="waiting for an instructor"):
        k.scan("T-0001")
    k.cancel()

    login(k, conn)
    with pytest.raises(Rejected, match="Say what you checked"):
        k.clear_quarantine("HHA-0001", "")
    k.clear_quarantine("HHA-0001", "new cord fitted")
    assert conn.execute("SELECT status FROM tools WHERE asset_id = 'HHA-0001'").fetchone()[0] == "available"
    assert conn.execute("SELECT resolved_by FROM damage_reports").fetchone()[0] == "P-9"
    assert queued(conn, "tool")[-1] == {"asset_id": "HHA-0001", "status": "available"}


def test_minor_damage_does_not_quarantine(k, conn):
    assert k.report_damage("HHA-0002", "minor", photo=False)["quarantined"] is False
    with pytest.raises(Rejected, match="Pick Minor"):
        k.report_damage("HHA-0002", "bad")


def test_worn_label_and_not_in_spot(k, conn):
    assert k.flag_label("HHA-0001", "Peeling")["tool"] == "Digital multimeter"
    with pytest.raises(Rejected, match="Faded, Scratched"):
        k.flag_label("HHA-0001", "Smudged")
    assert queued(conn, "reprint")[0]["condition"] == "Peeling"
    k.start("ID-1001", "out")
    k.scan("T-0001")  # still usable
    k.report_missing_from_spot("HHA-0002", "hook is empty")
    a = queued(conn, "alert")[-1]
    assert a["type"] == "not_in_spot" and a["last_person_name"] == "Jordan" and "hook is empty" in a["details"]
    assert conn.execute("SELECT spot_id FROM alerts").fetchone()[0] is None  # never folded into a camera alert


# -- K16 loans -----------------------------------------------------------------------------------------------

def test_due_time_is_earlier_of_loan_length_and_closing(conn):
    conn.execute("INSERT INTO settings VALUES ('loan_hours', '2'), ('lab_close', '23:59')")
    assert loans.due_at(conn, NOW) == NOW + timedelta(hours=2)
    local_close = (NOW + timedelta(minutes=30)).astimezone()
    conn.execute("UPDATE settings SET value = ? WHERE key = 'lab_close'", (f"{local_close:%H:%M}",))
    assert loans.due_at(conn, NOW) == NOW + timedelta(minutes=30)


def test_overdue_after_grace_and_extension(k, conn, wall):
    k.start("ID-1001", "out")
    k.scan("T-0001")
    due = datetime.fromisoformat(k.finish()["due_at"])
    wall.t = due + timedelta(minutes=10)
    assert loans.loans(conn, wall.t)[0]["overdue"] is False  # inside the 15-minute grace
    wall.t = due + timedelta(minutes=16)
    assert loans.loans(conn, wall.t)[0]["overdue"] is True
    login(k, conn)
    new = k.extend("HHA-0001", (wall.t + timedelta(days=2)).isoformat(), "taking it to a job site")
    assert loans.loans(conn, wall.t)[0]["overdue"] is False
    assert queued(conn, "extension")[0]["due_at"] == new["due_at"]
    k.start("ID-1001", "out")  # hold lifted


def test_hold_can_be_turned_off(k, conn, wall):
    k.start("ID-1001", "out")
    k.scan("T-0001")
    k.finish()
    wall.t += timedelta(days=1)
    conn.execute("INSERT INTO settings VALUES ('hold_overdue', 'no')")
    k.start("ID-1001", "out")


# -- API -------------------------------------------------------------------------------------------------

class FakeBuzzer:
    def __init__(self):
        self.played = []

    def play(self, s):
        self.played.append(s)


def test_api_sounds_and_kinds(k, conn):
    buzzer = FakeBuzzer()
    pins_on(conn)
    with TestClient(create_app(k, buzzer=buzzer)) as c:
        assert c.post("/api/code", json={"code": "ID-1001"}).json()["pin"] == "setup"
        assert "Choose a 4-digit PIN" in c.get("/api/session").json()["last_event"]["text"]
        assert c.post("/api/pin", json={"pin": "1357"}).json()["pin"] == "ok"
        c.post("/api/code", json={"code": "T-0010"})
        r = c.post("/api/code", json={"code": "B-2"})
        assert r.status_code == 409 and r.json()["kind"] == "wrong_battery" and r.json()["required"] == "DeWalt 20V Max"
        assert buzzer.played[-1] == "wrong_battery"
        assert c.post("/api/session/finish").status_code == 409
        c.post("/api/session/remove", json={"asset_id": "HHA-0010"})
        c.post("/api/damage", json={"asset_id": "T-0002", "severity": "needs_inspection", "photo": False})
        c.post("/api/label", json={"asset_id": "HHA-0002", "condition": "Torn"})
        st = c.get("/api/session").json()
        assert st["session"]["pin"] == "ok" and st["instructor"] is None
        assert c.get("/api/tools", params={"q": "gauge"}).json()[0]["state"] == "quarantined"
        assert c.get("/api/loans").json() == []
        assert c.post("/api/instructor/manual", json={"asset_id": "HHA-0001"}).status_code == 409
