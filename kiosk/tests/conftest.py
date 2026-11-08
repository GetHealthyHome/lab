import pytest

from lab_toolbox import db
from lab_toolbox.seed import DEMO


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "kiosk.db"))
    c.executescript(DEMO)
    return c


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()
