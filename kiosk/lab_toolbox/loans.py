"""Loan due times and overdue holds (K16).

Like a library's due-date stamp: a checkout is due after the standard loan length or at closing time,
whichever comes first. It only counts as overdue once the grace period has passed too.
"""
from datetime import datetime, timedelta, timezone

from .db import flag, setting

DEFAULT_LOAN_HOURS = 4.0      # PRD open question; set loan_hours in the Settings tab
DEFAULT_LAB_CLOSE = "21:00"   # kiosk local time
DEFAULT_LAB_OPEN = "07:00"    # only used to end the night for the screen dimmer
DEFAULT_GRACE_MINUTES = 15.0


def _num(conn, key: str, default: float) -> float:
    try:
        v = float(setting(conn, key, str(default)))
        return v if v >= 0 else default
    except ValueError:
        return default


def due_at(conn, now: datetime) -> datetime:
    """Due time for a checkout made at `now` (aware datetime)."""
    local = now.astimezone()  # the Pi's own time zone, set during setup
    loan_end = local + timedelta(hours=_num(conn, "loan_hours", DEFAULT_LOAN_HOURS) or DEFAULT_LOAN_HOURS)
    try:
        h, m = (int(x) for x in setting(conn, "lab_close", DEFAULT_LAB_CLOSE).split(":"))
        close = local.replace(hour=h, minute=m, second=0, microsecond=0)
    except ValueError:
        close = None
    # After closing time (a late night) the loan length alone applies.
    due = min(loan_end, close) if close and close > local else loan_end
    return due.astimezone(timezone.utc)


def _minutes(conn, key: str, default: str) -> int:
    """A Settings clock time such as 21:00 as minutes after midnight; the default if it's mistyped."""
    for v in (setting(conn, key, default), default):
        try:
            h, m = (int(x) for x in v.split(":"))
            return h * 60 + m
        except ValueError:
            pass


def closed(conn, now: datetime) -> bool:
    """True from lab_close until lab_open, kiosk local time: the hours when the screen may dim."""
    local = now.astimezone()
    t = local.hour * 60 + local.minute
    open_, close = _minutes(conn, "lab_open", DEFAULT_LAB_OPEN), _minutes(conn, "lab_close", DEFAULT_LAB_CLOSE)
    return not open_ <= t < close if open_ < close else close <= t < open_  # second case: a lab open past midnight


def grace(conn) -> timedelta:
    return timedelta(minutes=_num(conn, "grace_minutes", DEFAULT_GRACE_MINUTES))


def is_overdue(conn, due: str | None, now: datetime) -> bool:
    return bool(due) and now > datetime.fromisoformat(due) + grace(conn)


def loans(conn, now: datetime, person_id: str | None = None) -> list[dict]:
    """Tools out right now with their due time; overdue ones flagged (the screen shows them red)."""
    rows = conn.execute(
        "SELECT t.asset_id, t.name, t.holder_id, p.name AS holder, t.out_since, t.due_at FROM tool_state t"
        " LEFT JOIN people p ON p.person_id = t.holder_id WHERE t.last_action = 'out'"
        + (" AND t.holder_id = ?" if person_id else "") + " ORDER BY t.due_at",
        (person_id,) if person_id else (),
    ).fetchall()
    return [{**dict(r), "overdue": is_overdue(conn, r["due_at"], now)} for r in rows]


def held_for_overdue(conn, person_id: str, now: datetime) -> list[dict]:
    """Overdue tools that stop this person borrowing more, if the hold is on (default yes)."""
    if not flag(conn, "hold_overdue", True):
        return []
    return [l for l in loans(conn, now, person_id) if l["overdue"]]
