"""PIN login: 4 digits for students, 6 for instructors and supervisors. The PIN alone says who you are,
so every PIN is unique and the kiosk hands them out (an instructor taps Give a PIN, or `assign` below).

Stored as an HMAC with a secret key that never leaves the Pi, like a stamp only this kiosk owns: the same PIN
always makes the same mark (so the kiosk can look people up in one query), but the Sheet's copy can't be turned
back into PINs without the key. Lose the key file and everyone needs a new PIN.

Five wrong PINs in a row lock the keypad for a minute and open an alert.
"""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from datetime import timedelta
from pathlib import Path

MAX_TRIES = 5
LOCK_FOR = timedelta(minutes=1)
STAFF = ("instructor", "supervisor")


def length(role: str) -> int:
    return 6 if role in STAFF else 4


def valid(pin: str) -> bool:
    return bool(re.fullmatch(r"\d{4}|\d{6}", pin or ""))


def _key() -> bytes:
    # next to the database (/data on the Pi), which the kiosk user can write and the installer keeps
    path = Path(os.environ.get("PIN_KEY_FILE") or Path(os.environ.get("KIOSK_DB", "kiosk.db")).parent / "pin.key")
    if not path.exists():
        path.write_bytes(secrets.token_bytes(32))
        path.chmod(0o600)
    return path.read_bytes()


def hash_pin(pin: str) -> str:
    return "h1$" + hmac.new(_key(), pin.encode(), hashlib.sha256).hexdigest()


def find(conn: sqlite3.Connection, pin: str):
    """The active person with this PIN, or None."""
    return conn.execute("SELECT * FROM people WHERE pin_hash = ? AND active = 1", (hash_pin(pin),)).fetchone()


def new_pin(conn: sqlite3.Connection, role: str) -> tuple[str, str]:
    """A random PIN of the right length that nobody else has. Returns (pin, hash)."""
    n = length(role)
    for _ in range(200):
        pin = f"{secrets.randbelow(10 ** n):0{n}d}"
        h = hash_pin(pin)
        if not conn.execute("SELECT 1 FROM people WHERE pin_hash = ?", (h,)).fetchone():
            return pin, h
    raise RuntimeError("no free PIN found")  # only with thousands of students on 4 digits


if __name__ == "__main__":
    # First instructor PIN, on the Pi's own keyboard (see README): python -m lab_toolbox.pins P-9
    import sys

    from . import db
    from .sync import enqueue

    conn = db.connect(os.environ.get("KIOSK_DB", "kiosk.db"))
    person = conn.execute("SELECT * FROM people WHERE person_id = ?", (sys.argv[1],)).fetchone()
    if not person:
        sys.exit(f"No person {sys.argv[1]}")
    pin, h = new_pin(conn, person["role"])
    with db.tx(conn):
        conn.execute("UPDATE people SET pin_hash = ? WHERE person_id = ?", (h, person["person_id"]))
        enqueue(conn, "person", {"person_id": person["person_id"], "pin_hash": h})
    print(f"{person['name']}'s PIN is {pin}")
