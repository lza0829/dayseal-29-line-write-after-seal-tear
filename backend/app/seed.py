from __future__ import annotations
import sqlite3
from app.db import connect


SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tills (
  id INTEGER PRIMARY KEY,
  till_code TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cash_lines (
  id INTEGER PRIMARY KEY,
  line_no TEXT NOT NULL UNIQUE,
  till_code TEXT NOT NULL,
  business_day TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  amount REAL NOT NULL,
  pay_method TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS close_batches (
  id INTEGER PRIMARY KEY,
  batch_no TEXT NOT NULL UNIQUE,
  business_day TEXT NOT NULL,
  system_total REAL NOT NULL,
  declared_cash REAL NOT NULL,
  variance REAL NOT NULL,
  line_count INTEGER NOT NULL,
  status TEXT NOT NULL,
  sealed_at TEXT NOT NULL,
  note TEXT,
  voided_at TEXT,
  void_reason TEXT,
  superseded_by_batch_id INTEGER,
  line_fingerprint TEXT,
  header_fingerprint TEXT
);
CREATE TABLE IF NOT EXISTS variance_rows (
  id INTEGER PRIMARY KEY,
  batch_id INTEGER NOT NULL,
  business_day TEXT NOT NULL,
  variance REAL NOT NULL,
  disposition TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS variance_holds (
  id INTEGER PRIMARY KEY,
  business_day TEXT NOT NULL,
  reason TEXT
);
CREATE TABLE IF NOT EXISTS reopen_requests (
  id INTEGER PRIMARY KEY,
  business_day TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'none',
  batch_id INTEGER,
  reason TEXT,
  created_at TEXT
);

-- Frozen effective set of a sealed batch. The snapshot is the rollback target
-- for any write that slipped past the seal gate: restoring these rows restores
-- the exact line set the batch was pinned to.
CREATE TABLE IF NOT EXISTS sealed_line_snapshot (
  batch_id INTEGER NOT NULL,
  line_id INTEGER NOT NULL,
  line_no TEXT NOT NULL,
  till_code TEXT NOT NULL,
  business_day TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  amount REAL NOT NULL,
  pay_method TEXT NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (batch_id, line_id)
);

-- One row per attempted or repaired write against a sealed partition. Both
-- rejected attempts and automatic rollbacks land here so the conflict is
-- auditable instead of silently disappearing.
CREATE TABLE IF NOT EXISTS write_conflicts (
  id INTEGER PRIMARY KEY,
  business_day TEXT NOT NULL,
  batch_id INTEGER,
  path TEXT NOT NULL,
  outcome TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL
);

-- Cash pickups ("解款") are ancillary writes to a business day and obey the
-- same seal gate as the cash lines themselves.
CREATE TABLE IF NOT EXISTS cash_pickups (
  id INTEGER PRIMARY KEY,
  business_day TEXT NOT NULL,
  till_code TEXT NOT NULL,
  amount REAL NOT NULL,
  note TEXT,
  created_at TEXT NOT NULL
);
'''


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def migrate(conn: sqlite3.Connection) -> None:
    """Bring databases created before the seal-freeze policy up to shape."""
    _add_column(conn, "close_batches", "voided_at", "TEXT")
    _add_column(conn, "close_batches", "void_reason", "TEXT")
    _add_column(conn, "close_batches", "superseded_by_batch_id", "INTEGER")
    _add_column(conn, "close_batches", "line_fingerprint", "TEXT")
    _add_column(conn, "close_batches", "header_fingerprint", "TEXT")
    _add_column(conn, "reopen_requests", "batch_id", "INTEGER")
    _add_column(conn, "reopen_requests", "reason", "TEXT")
    _add_column(conn, "reopen_requests", "created_at", "TEXT")

    # At most one current (sealed) batch per business day. Legacy databases that
    # already hold duplicates cannot get the index; the application-level check
    # inside BEGIN IMMEDIATE still serialises seal()/reopen()/write gates.
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_close_batches_sealed_day "
            "ON close_batches(business_day) WHERE status='sealed'"
        )
    except sqlite3.IntegrityError:
        pass


def seed_if_empty() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        migrate(conn)
        n = conn.execute("SELECT COUNT(*) c FROM tills").fetchone()["c"]
        if n:
            return
        conn.execute("INSERT INTO settings(key, value) VALUES ('business_day', ?)", ("2026-11-16",))
        tills = [
            ("TILL-A", "一号收银"),
            ("TILL-B", "二号收银"),
            ("TILL-C", "三号收银"),
        ]
        for code, title in tills:
            conn.execute("INSERT INTO tills(till_code, title) VALUES (?,?)", (code, title))
        lines = [
            ("CL-0001", "TILL-A", "2026-11-16", "2026-11-16T09:12:00Z", 35.5, "现金", "早市"),
            ("CL-0002", "TILL-A", "2026-11-16", "2026-11-16T10:05:00Z", 128.0, "微信", None),
            ("CL-0003", "TILL-B", "2026-11-16", "2026-11-16T11:20:00Z", 66.0, "现金", None),
            ("CL-0004", "TILL-B", "2026-11-16", "2026-11-16T14:02:00Z", 210.0, "支付宝", None),
            ("CL-0005", "TILL-C", "2026-11-16", "2026-11-16T16:40:00Z", 49.9, "现金", None),
            ("CL-0006", "TILL-A", "2026-11-15", "2026-11-15T18:10:00Z", 88.0, "现金", "昨日"),
        ]
        for row in lines:
            conn.execute(
                '''INSERT INTO cash_lines(line_no, till_code, business_day, occurred_at, amount, pay_method, note, created_at)
                   VALUES (?,?,?,?,?,?,?,?)''',
                (*row, row[3]),
            )
        conn.commit()
    finally:
        conn.close()
