from __future__ import annotations

import sqlite3
import time

from app.config import DATA_DIR, DB_PATH

BUSY_TIMEOUT_MS = 30000
BEGIN_DEADLINE_S = 30.0


def connect(*, immediate: bool = False) -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Autocommit mode; write paths opt into an explicit BEGIN IMMEDIATE so that
    # the seal check, the mutation and the audit row share one serialised unit.
    conn = sqlite3.connect(DB_PATH, isolation_level=None, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    # WAL lets readers (dashboards, previews) proceed while a writer holds the
    # write lock; the seal gate still serialises writers via BEGIN IMMEDIATE.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    if immediate:
        _begin_immediate(conn)
    return conn


def _begin_immediate(conn: sqlite3.Connection) -> None:
    """Acquire the write lock, retrying past a busy timeout.

    Under a burst of concurrent writes the busy handler can still surface
    "database is locked" once its wait expires; rather than 500 the request we
    keep retrying until the deadline, then let the error propagate.
    """
    deadline = time.monotonic() + BEGIN_DEADLINE_S
    while True:
        try:
            conn.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
