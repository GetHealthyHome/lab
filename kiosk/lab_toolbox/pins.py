"""4-digit PINs (K11). Stored as Argon2id hashes, like a lock that only says yes or no and never shows the key.

Five wrong tries lock that ID for 15 minutes and raise a security alert.
"""
import re
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# Light enough for the Pi 3B+ (about a quarter of a second), still Argon2id as the PRD asks.
_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
MAX_TRIES = 5
LOCK_FOR = timedelta(minutes=15)


def valid(pin: str) -> bool:
    return bool(re.fullmatch(r"\d{4}", pin or ""))


def hash_pin(pin: str) -> str:
    return _hasher.hash(pin)


def check(pin_hash: str, pin: str) -> bool:
    try:
        return _hasher.verify(pin_hash, pin)
    except (VerificationError, InvalidHashError):
        return False


def locked_until(person, now: datetime) -> datetime | None:
    until = person["locked_until"]
    if until and datetime.fromisoformat(until) > now:
        return datetime.fromisoformat(until)
    return None
