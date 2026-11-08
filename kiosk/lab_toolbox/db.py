"""Local SQLite store. The Pi's copy of every record; Google gets a copy later via the sync queue."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

# Each entry runs once, in order; PRAGMA user_version remembers how far we got.
MIGRATIONS = [
    """
    CREATE TABLE spots (
        spot_id TEXT PRIMARY KEY,          -- LOC-S03-B02-H04
        label TEXT NOT NULL,               -- "Shelf 03, bay 02, hook 04"
        box TEXT,                          -- JSON [x, y, w, h] on the reference photo
        reference_photo TEXT
    );
    CREATE TABLE tools (
        asset_id TEXT PRIMARY KEY,         -- engraved number
        qr_code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        category TEXT,
        spot_id TEXT REFERENCES spots(spot_id),
        -- Admin-set state only. "out" is never stored: it comes from the latest transaction (see tool_state).
        status TEXT NOT NULL DEFAULT 'available' CHECK (status IN ('available', 'quarantined', 'retired')),
        notes TEXT
    );
    CREATE TABLE people (
        person_id TEXT PRIMARY KEY,
        id_code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('student', 'instructor', 'supervisor')),
        pin_hash TEXT,
        active INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE transactions (
        txn_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        asset_id TEXT NOT NULL REFERENCES tools(asset_id),
        person_id TEXT NOT NULL REFERENCES people(person_id),
        action TEXT NOT NULL CHECK (action IN ('out', 'in')),
        session_id TEXT NOT NULL,
        gate_photo TEXT,
        override_by TEXT REFERENCES people(person_id),
        kiosk_id TEXT NOT NULL
    );
    CREATE INDEX transactions_asset ON transactions(asset_id);
    CREATE TABLE snapshots (
        snapshot_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        photo TEXT,
        spots TEXT NOT NULL                -- JSON {spot_id: "full" | "empty" | "unclear"}
    );
    CREATE TABLE alerts (
        alert_id TEXT PRIMARY KEY,
        opened TEXT NOT NULL,
        type TEXT NOT NULL,
        spot_id TEXT REFERENCES spots(spot_id),
        asset_id TEXT REFERENCES tools(asset_id),
        last_person_id TEXT REFERENCES people(person_id),
        last_checkout TEXT,
        snapshot_photo TEXT,
        gate_photo TEXT,
        status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'acknowledged', 'resolved', 'false_alarm')),
        resolved_by TEXT REFERENCES people(person_id),
        resolution_note TEXT
    );
    CREATE TABLE audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        who TEXT NOT NULL,
        tab TEXT NOT NULL,
        row TEXT NOT NULL,
        field TEXT NOT NULL,
        old_value TEXT,
        new_value TEXT,
        reason TEXT NOT NULL
    );
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    -- Outbox: written in the same DB transaction as the record, sent to Google when online (K10).
    CREATE TABLE sync_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,                -- transaction | photo | ...
        payload TEXT NOT NULL,             -- JSON
        created_at TEXT NOT NULL,
        sent_at TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT
    );
    CREATE INDEX sync_queue_unsent ON sync_queue(id) WHERE sent_at IS NULL;

    -- Append-only logs: nothing in them is edited or deleted.
    CREATE TRIGGER transactions_no_update BEFORE UPDATE ON transactions BEGIN SELECT RAISE(ABORT, 'transactions is append-only'); END;
    CREATE TRIGGER transactions_no_delete BEFORE DELETE ON transactions BEGIN SELECT RAISE(ABORT, 'transactions is append-only'); END;
    CREATE TRIGGER snapshots_no_update BEFORE UPDATE ON snapshots BEGIN SELECT RAISE(ABORT, 'snapshots is append-only'); END;
    CREATE TRIGGER snapshots_no_delete BEFORE DELETE ON snapshots BEGIN SELECT RAISE(ABORT, 'snapshots is append-only'); END;
    CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
    CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

    -- A tool's live state: admin status wins, otherwise "out" if its latest transaction is a checkout.
    CREATE VIEW tool_state AS
    SELECT t.*,
           CASE WHEN t.status != 'available' THEN t.status
                WHEN last.action = 'out' THEN 'out'
                ELSE 'available' END AS state,
           CASE WHEN last.action = 'out' THEN last.person_id END AS holder_id,
           CASE WHEN last.action = 'out' THEN last.ts END AS out_since
    FROM tools t
    LEFT JOIN (
        -- SQLite returns the other columns from the row holding MAX(rowid): the latest transaction.
        SELECT asset_id, action, person_id, ts, MAX(rowid) FROM transactions GROUP BY asset_id
    ) last ON last.asset_id = t.asset_id;
    """,
    # Local photo path -> its Drive copy, so Sheet rows get the link and a retried upload is not duplicated.
    "CREATE TABLE drive_photos (path TEXT PRIMARY KEY, file_id TEXT NOT NULL, link TEXT NOT NULL)",
    # Cage monitor: when an open alert was last confirmed by a snapshot (C6 updates instead of repeating).
    "ALTER TABLE alerts ADD COLUMN last_seen TEXT",
    # K7, K8, K11-K17: PINs and lockout, batteries, kits, damage, worn labels, loan due times.
    """
    ALTER TABLE people ADD COLUMN email TEXT;
    ALTER TABLE people ADD COLUMN failed_pin_attempts INTEGER NOT NULL DEFAULT 0;  -- local only, never synced
    ALTER TABLE people ADD COLUMN locked_until TEXT;
    ALTER TABLE tools ADD COLUMN serial TEXT;
    ALTER TABLE tools ADD COLUMN brand TEXT;
    ALTER TABLE tools ADD COLUMN model TEXT;
    ALTER TABLE tools ADD COLUMN needs_battery INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE tools ADD COLUMN battery_group_id TEXT;
    ALTER TABLE tools ADD COLUMN is_battery INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE transactions ADD COLUMN paired_battery_id TEXT;
    ALTER TABLE transactions ADD COLUMN kit_id TEXT;
    ALTER TABLE transactions ADD COLUMN due_at TEXT;
    ALTER TABLE alerts ADD COLUMN details TEXT;
    CREATE TABLE battery_groups (group_id TEXT PRIMARY KEY, name TEXT NOT NULL);  -- "DeWalt 20V Max"
    CREATE TABLE kits (
        kit_id TEXT PRIMARY KEY,
        case_code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        spot_id TEXT,
        check_mode TEXT NOT NULL DEFAULT 'standard' CHECK (check_mode IN ('standard', 'verified')),
        -- "out" is derived from its pieces' transactions, like a tool's
        status TEXT NOT NULL DEFAULT 'available' CHECK (status IN ('available', 'incomplete'))
    );
    CREATE TABLE kit_items (
        kit_id TEXT NOT NULL REFERENCES kits(kit_id),
        asset_id TEXT NOT NULL REFERENCES tools(asset_id),
        PRIMARY KEY (kit_id, asset_id)
    );
    CREATE TABLE damage_reports (
        report_id TEXT PRIMARY KEY,
        asset_id TEXT NOT NULL REFERENCES tools(asset_id),
        reported_by TEXT REFERENCES people(person_id),
        severity TEXT NOT NULL CHECK (severity IN ('minor', 'needs_inspection', 'safety_hazard')),
        tags TEXT,
        notes TEXT,
        photo TEXT,
        opened TEXT NOT NULL,
        resolved_by TEXT,
        resolution TEXT,
        resolved_at TEXT
    );
    CREATE TABLE reprint_queue (
        reprint_id TEXT PRIMARY KEY,
        asset_id TEXT NOT NULL REFERENCES tools(asset_id),
        reported_by TEXT REFERENCES people(person_id),
        condition TEXT NOT NULL CHECK (condition IN ('Faded', 'Scratched', 'Peeling', 'Torn')),
        flagged_at TEXT NOT NULL
    );
    -- K16: an instructor's new due time for one checkout. Transactions stay append-only.
    CREATE TABLE loan_extensions (
        txn_id TEXT NOT NULL REFERENCES transactions(txn_id),
        due_at TEXT NOT NULL,
        by TEXT NOT NULL,
        ts TEXT NOT NULL,
        reason TEXT
    );
    DROP VIEW tool_state;
    CREATE VIEW tool_state AS
    SELECT t.*,
           CASE WHEN t.status != 'available' THEN t.status
                WHEN last.action = 'out' THEN 'out'
                ELSE 'available' END AS state,
           last.action AS last_action,
           CASE WHEN last.action = 'out' THEN last.person_id END AS holder_id,
           CASE WHEN last.action = 'out' THEN last.ts END AS out_since,
           CASE WHEN last.action = 'out' THEN last.txn_id END AS out_txn_id,
           CASE WHEN last.action = 'out' THEN COALESCE(
               (SELECT e.due_at FROM loan_extensions e WHERE e.txn_id = last.txn_id ORDER BY e.rowid DESC LIMIT 1),
               last.due_at) END AS due_at
    FROM tools t
    LEFT JOIN (
        SELECT asset_id, action, person_id, ts, txn_id, due_at, MAX(rowid) FROM transactions GROUP BY asset_id
    ) last ON last.asset_id = t.asset_id;
    """,
]


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # PRD "Stability on the Pi": WAL, synchronous NORMAL, foreign keys, 5 s busy timeout.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(f"BEGIN; {sql}; PRAGMA user_version = {i}; COMMIT;")


def setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    """A Settings value (synced down from the Sheet), or the default when it's missing or blank."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row and str(row["value"]).strip() != "" else default


def flag(conn: sqlite3.Connection, key: str, default: bool) -> bool:
    v = setting(conn, key)
    return default if v is None else v.strip().lower() not in ("no", "false", "0", "off", "n")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def tx(conn: sqlite3.Connection):
    """All-or-nothing write: a power cut mid-session never leaves half a checkout."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
