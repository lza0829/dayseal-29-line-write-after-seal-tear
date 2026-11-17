from __future__ import annotations
import sqlite3
from typing import Any


def list_lines(conn: sqlite3.Connection, business_day: str | None = None) -> list[dict[str, Any]]:
    if business_day:
        rows = conn.execute(
            "SELECT * FROM cash_lines WHERE business_day=? ORDER BY occurred_at, id",
            (business_day,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM cash_lines ORDER BY business_day DESC, occurred_at, id").fetchall()
    return [dict(r) for r in rows]


def day_total(conn: sqlite3.Connection, business_day: str) -> tuple[float, int]:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount),0) s, COUNT(*) c FROM cash_lines WHERE business_day=?",
        (business_day,),
    ).fetchone()
    return float(row["s"]), int(row["c"])


def next_line_no(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(line_no, 4) AS INTEGER)) n FROM cash_lines WHERE line_no LIKE 'CL-%'"
    ).fetchone()
    n = int(row["n"] or 0) + 1
    return f"CL-{n:04d}"


def insert_line(conn: sqlite3.Connection, **fields: Any) -> int:
    cur = conn.execute(
        '''INSERT INTO cash_lines(line_no, till_code, business_day, occurred_at, amount, pay_method, note, created_at)
           VALUES (?,?,?,?,?,?,?,?)''',
        (
            fields["line_no"],
            fields["till_code"],
            fields["business_day"],
            fields["occurred_at"],
            fields["amount"],
            fields["pay_method"],
            fields.get("note"),
            fields["created_at"],
        ),
    )
    return int(cur.lastrowid)


def update_line(conn: sqlite3.Connection, line_id: int, **fields: Any) -> None:
    conn.execute(
        "UPDATE cash_lines SET amount=?, pay_method=?, note=? WHERE id=?",
        (fields["amount"], fields["pay_method"], fields.get("note"), line_id),
    )


def move_line_day(conn: sqlite3.Connection, line_id: int, business_day: str) -> None:
    conn.execute("UPDATE cash_lines SET business_day=? WHERE id=?", (business_day, line_id))


def delete_line(conn: sqlite3.Connection, line_id: int) -> None:
    conn.execute("DELETE FROM cash_lines WHERE id=?", (line_id,))


def get_line(conn: sqlite3.Connection, line_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM cash_lines WHERE id=?", (line_id,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Pickups ("解款") — ancillary cash writes, held to the same seal gate
# ---------------------------------------------------------------------------

def insert_pickup(conn: sqlite3.Connection, **fields: Any) -> int:
    cur = conn.execute(
        '''INSERT INTO cash_pickups(business_day, till_code, amount, note, created_at)
           VALUES (?,?,?,?,?)''',
        (
            fields["business_day"],
            fields["till_code"],
            fields["amount"],
            fields.get("note"),
            fields["created_at"],
        ),
    )
    return int(cur.lastrowid)


def get_pickup(conn: sqlite3.Connection, pickup_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM cash_pickups WHERE id=?", (pickup_id,)).fetchone()
    return dict(row) if row else None


def list_pickups(conn: sqlite3.Connection, business_day: str | None = None) -> list[dict[str, Any]]:
    if business_day:
        rows = conn.execute(
            "SELECT * FROM cash_pickups WHERE business_day=? ORDER BY id", (business_day,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM cash_pickups ORDER BY business_day DESC, id").fetchall()
    return [dict(r) for r in rows]


def pickup_total(conn: sqlite3.Connection, business_day: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount),0) s FROM cash_pickups WHERE business_day=?", (business_day,)
    ).fetchone()
    return float(row["s"])
