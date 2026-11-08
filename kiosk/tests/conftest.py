import pytest

from lab_toolbox import db
from lab_toolbox.seed import DEMO


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "kiosk.db"))
    c.executescript(DEMO)
    # PIN checks (K11) are on by default; tests that don't exercise them turn them off. test_pins turns them back on.
    c.execute("INSERT INTO settings VALUES ('require_pin', 'no')")
    return c


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()
