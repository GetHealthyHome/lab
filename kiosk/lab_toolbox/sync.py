"""Offline outbox (K10): like a notebook at the counter copied into the master ledger later.

Records are queued in the same DB transaction that saves them, then sent in order when Google is reachable.
"""
import json
import os
import sqlite3
from typing import Any, Protocol

from .db import now_iso


class Sink(Protocol):
    """Where queued items go. The Google Sheets/Drive sink implements this; tests use a fake."""

    def send(self, kind: str, payload: dict[str, Any]) -> None: ...


def enqueue(conn: sqlite3.Connection, kind: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO sync_queue (kind, payload, created_at) VALUES (?, ?, ?)",
        (kind, json.dumps(payload), now_iso()),
    )


def pending(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM sync_queue WHERE sent_at IS NULL").fetchone()[0]


def sync_once(conn: sqlite3.Connection, sink: Sink, limit: int = 100) -> int:
    """Send up to `limit` items oldest first. Stops at the first failure so order is kept. Returns items sent."""
    rows = conn.execute(
        "SELECT id, kind, payload FROM sync_queue WHERE sent_at IS NULL ORDER BY id LIMIT ?", (limit,)
    ).fetchall()
    sent = 0
    for row in rows:
        payload = json.loads(row["payload"])
        try:
            sink.send(row["kind"], payload)
        except Exception as e:  # network down, quota, auth: keep it queued and try again next round
            conn.execute(
                "UPDATE sync_queue SET attempts = attempts + 1, last_error = ? WHERE id = ?",
                (f"{type(e).__name__}: {e}"[:500], row["id"]),
            )
            break
        conn.execute("UPDATE sync_queue SET sent_at = ?, attempts = attempts + 1 WHERE id = ?", (now_iso(), row["id"]))
        if row["kind"] == "photo" and not payload.get("keep"):  # reference and tool photos stay: the Pi still uses them
            # PRD Storage: the Pi deletes its local copy once Drive has it.
            try:
                os.remove(payload["path"])
            except FileNotFoundError:
                pass
        sent += 1
    return sent
