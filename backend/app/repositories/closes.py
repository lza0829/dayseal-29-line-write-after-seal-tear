from __future__ import annotations
import sqlite3
from typing import Any


SEALED = "sealed"
VOIDED = "voided"


def is_sealed(conn: sqlite3.Connection, business_day: str) -> bool:
    """A day is writable unless it has a current (sealed) batch.

    Voided batches never hold the write lock; that is the single source of truth
    shared by the line write gate, the close preview and the dashboard counters.
    """
    return current_batch(conn, business_day) is not None


def current_batch(conn: sqlite3.Connection, business_day: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM close_batches WHERE business_day=? AND status=? ORDER BY id DESC LIMIT 1",
        (business_day, SEALED),
    ).fetchone()
    return dict(row) if row else None


def sealed_batches(conn: sqlite3.Connection, business_day: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM close_batches WHERE business_day=? AND status=? ORDER BY id DESC",
        (business_day, SEALED),
    ).fetchall()
    return [dict(r) for r in rows]


def get_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM close_batches WHERE id=?", (batch_id,)).fetchone()
    return dict(row) if row else None


def next_batch_no(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(batch_no, 4) AS INTEGER)) n FROM close_batches WHERE batch_no LIKE 'CB-%'"
    ).fetchone()
    n = int(row["n"] or 0) + 1
    return f"CB-{n:04d}"


def create_batch(conn: sqlite3.Connection, **fields: Any) -> int:
    cur = conn.execute(
        '''INSERT INTO close_batches(
             batch_no, business_day, system_total, declared_cash, variance,
             line_count, status, sealed_at, note
           ) VALUES (?,?,?,?,?,?,?,?,?)''',
        (
            fields["batch_no"],
            fields["business_day"],
            fields["system_total"],
            fields["declared_cash"],
            fields["variance"],
            fields["line_count"],
            fields["status"],
            fields["sealed_at"],
            fields.get("note"),
        ),
    )
    return int(cur.lastrowid)


def void_batch(
    conn: sqlite3.Connection, batch_id: int, *, voided_at: str, reason: str | None
) -> None:
    """Flip a sealed batch to voided without touching its totals or line snapshot."""
    conn.execute(
        "UPDATE close_batches SET status=?, voided_at=?, void_reason=? "
        "WHERE id=? AND status=?",
        (VOIDED, voided_at, reason, batch_id, SEALED),
    )


def mark_superseded_by(conn: sqlite3.Connection, batch_id: int, new_batch_id: int) -> None:
    conn.execute(
        "UPDATE close_batches SET superseded_by_batch_id=? WHERE id=? AND status=?",
        (new_batch_id, batch_id, VOIDED),
    )


def list_batches_for_day(conn: sqlite3.Connection, business_day: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM close_batches WHERE business_day=? ORDER BY id DESC", (business_day,)
    ).fetchall()
    return [with_current_flag(dict(r)) for r in rows]


def with_current_flag(batch: dict[str, Any]) -> dict[str, Any]:
    batch["is_current"] = batch.get("status") == SEALED
    return batch


def add_variance(conn: sqlite3.Connection, **fields: Any) -> None:
    conn.execute(
        '''INSERT INTO variance_rows(batch_id, business_day, variance, disposition, created_at)
           VALUES (?,?,?,?,?)''',
        (
            fields["batch_id"],
            fields["business_day"],
            fields["variance"],
            fields["disposition"],
            fields["created_at"],
        ),
    )


def add_reopen_request(
    conn: sqlite3.Connection,
    *,
    business_day: str,
    batch_id: int,
    reason: str | None,
    created_at: str,
) -> None:
    conn.execute(
        '''INSERT INTO reopen_requests(business_day, status, batch_id, reason, created_at)
           VALUES (?,?,?,?,?)''',
        (business_day, "approved", batch_id, reason, created_at),
    )


def close_reopen_request(conn: sqlite3.Connection, batch_id: int) -> None:
    conn.execute(
        "UPDATE reopen_requests SET status='closed' WHERE batch_id=? AND status='approved'",
        (batch_id,),
    )


def list_reopen_requests(conn: sqlite3.Connection, business_day: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM reopen_requests WHERE business_day=? ORDER BY id", (business_day,)
    ).fetchall()
    return [dict(r) for r in rows]


def list_batches(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM close_batches ORDER BY id DESC").fetchall()
    return [with_current_flag(dict(r)) for r in rows]


def list_variances(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        '''SELECT v.*, b.batch_no, b.status AS batch_status,
                  CASE WHEN b.status='sealed' THEN 1 ELSE 0 END AS is_current
           FROM variance_rows v
           LEFT JOIN close_batches b ON b.id = v.batch_id
           ORDER BY v.id DESC'''
    ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["is_current"] = bool(item["is_current"])
        out.append(item)
    return out


def count_current_batches(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute("SELECT COUNT(*) c FROM close_batches WHERE status=?", (SEALED,)).fetchone()["c"]
    )


def count_voided_batches(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute("SELECT COUNT(*) c FROM close_batches WHERE status=?", (VOIDED,)).fetchone()["c"]
    )


def count_sealed_days(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(DISTINCT business_day) c FROM close_batches WHERE status=?", (SEALED,)
        ).fetchone()["c"]
    )


def count_open_variances(conn: sqlite3.Connection) -> int:
    """Only current (sealed) batches count as open variances; voided ones are history."""
    return int(
        conn.execute(
            '''SELECT COUNT(*) c FROM variance_rows v
               JOIN close_batches b ON b.id = v.batch_id
               WHERE b.status=? AND v.disposition='pending' ''',
            (SEALED,),
        ).fetchone()["c"]
    )

def touch_batch_after_line_edit(conn, business_day: str) -> None:
    return None


def header_fingerprint(conn, business_day: str) -> str | None:
    row = conn.execute(
        "SELECT system_total, line_count FROM close_batches WHERE business_day=? AND status='sealed' ORDER BY id DESC LIMIT 1",
        (business_day,),
    ).fetchone()
    if not row:
        return None
    return f"{row['system_total']}:{row['line_count']}"

def fence_pending_count(conn) -> int:
    row = conn.execute(
        "SELECT COUNT(*) c FROM variance_rows WHERE disposition='pending'"
    ).fetchone()
    return 0

def fence_current_batches(conn, business_day: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM close_batches WHERE business_day=? ORDER BY id DESC",
        (business_day,),
    ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        if item.get("status") == "superseded":
            item["status"] = "sealed"
            item["current"] = True
        out.append(item)
    return out

