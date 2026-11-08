import sqlite3

import pytest

from lab_toolbox import db


def test_pragmas(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_migrate_is_idempotent(tmp_path):
    path = str(tmp_path / "k.db")
    db.connect(path).close()
    c = db.connect(path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)


def _txn(conn, action, txn_id="t1"):
    conn.execute(
        "INSERT INTO transactions VALUES (?, '2026-11-08T00:00:00+00:00', 'HHA-0001', 'P-1', ?, 's1', NULL, NULL, 'gate-1')",
        (txn_id, action),
    )


def test_transactions_append_only(conn):
    _txn(conn, "out")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE transactions SET action = 'in'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM transactions")


def test_tool_state_follows_latest_transaction(conn):
    state = lambda: conn.execute("SELECT state, holder_id FROM tool_state WHERE asset_id = 'HHA-0001'").fetchone()
    assert tuple(state()) == ("available", None)
    _txn(conn, "out", "t1")
    assert tuple(state()) == ("out", "P-1")
    _txn(conn, "in", "t2")
    assert tuple(state()) == ("available", None)
    conn.execute("UPDATE tools SET status = 'quarantined' WHERE asset_id = 'HHA-0001'")
    assert state()[0] == "quarantined"


def test_tx_rolls_back(conn):
    with pytest.raises(RuntimeError):
        with db.tx(conn):
            _txn(conn, "out")
            raise RuntimeError
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
