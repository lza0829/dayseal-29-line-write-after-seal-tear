from __future__ import annotations
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable


SEALED = "sealed"
VOIDED = "voided"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Current / sealed batch lookups — the single source of truth for the write gate
# ---------------------------------------------------------------------------

def is_sealed(conn: sqlite3.Connection, business_day: str) -> bool:
    """A day is writable unless it has a current (sealed) batch.

    Voided batches never hold the write lock; that is the single source of truth
    shared by every write entry point, the close preview and the dashboard counters.
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


# ---------------------------------------------------------------------------
# Fingerprints — a sealed batch pins both its header and its exact line set
# ---------------------------------------------------------------------------

def _line_tuple(row: dict[str, Any] | sqlite3.Row) -> tuple[Any, ...]:
    # repr() of the stored double is exact and round-trips through SQLite, so a
    # sub-cent change is detected instead of being masked by 2dp rounding.
    return (
        int(row["id"]),
        str(row["line_no"]),
        str(row["till_code"]),
        str(row["business_day"]),
        str(row["occurred_at"]),
        repr(float(row["amount"])),
        str(row["pay_method"]),
        str(row["note"] or ""),
    )


def line_fingerprint_of(rows: Iterable[dict[str, Any] | sqlite3.Row]) -> str:
    payload = [list(_line_tuple(r)) for r in sorted(rows, key=lambda r: int(r["id"]))]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def header_fingerprint_of(
    *, batch_no: str, business_day: str, system_total: float, line_count: int, variance: float
) -> str:
    token = f"{batch_no}|{business_day}|{float(system_total):.2f}|{int(line_count)}|{float(variance):.2f}"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def live_line_fingerprint(conn: sqlite3.Connection, business_day: str) -> str:
    rows = conn.execute(
        "SELECT * FROM cash_lines WHERE business_day=? ORDER BY id", (business_day,)
    ).fetchall()
    return line_fingerprint_of(rows)


def header_fingerprint(conn: sqlite3.Connection, business_day: str) -> str | None:
    """Fingerprint of the current sealed batch header, or None when unsealed."""
    batch = current_batch(conn, business_day)
    if not batch:
        return None
    stored = batch.get("header_fingerprint")
    if stored:
        return str(stored)
    return header_fingerprint_of(
        batch_no=batch["batch_no"],
        business_day=batch["business_day"],
        system_total=batch["system_total"],
        line_count=batch["line_count"],
        variance=batch["variance"],
    )


# ---------------------------------------------------------------------------
# Snapshot of the sealed effective set
# ---------------------------------------------------------------------------

def snapshot_lines(conn: sqlite3.Connection, batch_id: int, business_day: str) -> str:
    """Freeze the current line set under the batch and return its fingerprint."""
    rows = conn.execute(
        "SELECT * FROM cash_lines WHERE business_day=? ORDER BY id", (business_day,)
    ).fetchall()
    conn.execute("DELETE FROM sealed_line_snapshot WHERE batch_id=?", (batch_id,))
    for r in rows:
        conn.execute(
            '''INSERT INTO sealed_line_snapshot(
                 batch_id, line_id, line_no, till_code, business_day, occurred_at,
                 amount, pay_method, note, created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (
                batch_id,
                r["id"],
                r["line_no"],
                r["till_code"],
                r["business_day"],
                r["occurred_at"],
                r["amount"],
                r["pay_method"],
                r["note"],
                r["created_at"],
            ),
        )
    return line_fingerprint_of(rows)


def snapshot_rows(conn: sqlite3.Connection, batch_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM sealed_line_snapshot WHERE batch_id=? ORDER BY line_id", (batch_id,)
    ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["id"] = item["line_id"]  # allow the same fingerprint code path
        out.append(item)
    return out


def ensure_snapshot(
    conn: sqlite3.Connection, batch: dict[str, Any], *, live_fp: str | None = None
) -> tuple[str, bool]:
    """Return ``(frozen_fingerprint, trustworthy)`` for a sealed batch.

    Sealing always writes a snapshot going forward. A database created before
    the freeze existed may have neither a snapshot nor a fingerprint; we adopt
    the live set only when it still agrees with the sealed header. If the header
    and the live set disagree, the original set cannot be reconstructed, so we
    report ``trustworthy=False`` rather than freezing already-drifted data as
    the truth (that would make the leak permanent instead of reversible).
    """
    stored = batch.get("line_fingerprint")
    existing = snapshot_rows(conn, batch["id"])
    if stored and existing:
        return str(stored), True

    if live_fp is None:
        live_fp = live_line_fingerprint(conn, batch["business_day"])
    rows = conn.execute(
        "SELECT * FROM cash_lines WHERE business_day=? ORDER BY id", (batch["business_day"],)
    ).fetchall()
    total = round(sum(float(r["amount"]) for r in rows), 2)
    header_matches = (
        round(float(batch["system_total"]), 2) == total
        and int(batch["line_count"]) == len(rows)
    )

    if existing and not stored:
        fp = line_fingerprint_of(existing)
        _persist_fingerprints(conn, batch, fp)
        return fp, True
    if stored and not existing:
        # A fingerprint without a snapshot can only be trusted if the live set
        # still hashes to it; otherwise the original rows are gone.
        if str(stored) == live_fp:
            snapshot_lines(conn, batch["id"], batch["business_day"])
            _persist_fingerprints(conn, batch, str(stored))
            return str(stored), True
        return str(stored), False
    if header_matches:
        fp = snapshot_lines(conn, batch["id"], batch["business_day"])
        _persist_fingerprints(conn, batch, fp)
        return fp, True
    return live_fp, False


def _persist_fingerprints(
    conn: sqlite3.Connection, batch: dict[str, Any], line_fp: str
) -> None:
    header_fp = batch.get("header_fingerprint") or header_fingerprint_of(
        batch_no=batch["batch_no"],
        business_day=batch["business_day"],
        system_total=batch["system_total"],
        line_count=batch["line_count"],
        variance=batch["variance"],
    )
    conn.execute(
        "UPDATE close_batches SET line_fingerprint=?, header_fingerprint=? WHERE id=?",
        (line_fp, header_fp, batch["id"]),
    )


def _evict_line_no(conn: sqlite3.Connection, line_no: str, keep_id: int) -> None:
    """Free ``line_no`` for its frozen owner without ever colliding itself."""
    while True:
        clash = conn.execute(
            "SELECT id FROM cash_lines WHERE line_no=? AND id<>?", (line_no, keep_id)
        ).fetchone()
        if not clash:
            return
        clash_id = int(clash["id"])
        candidate = f"CL-EVICT-{clash_id}"
        n = 0
        while conn.execute("SELECT 1 FROM cash_lines WHERE line_no=?", (candidate,)).fetchone():
            n += 1
            candidate = f"CL-EVICT-{clash_id}-{n}"
        conn.execute("UPDATE cash_lines SET line_no=? WHERE id=?", (candidate, clash_id))


def _restore_line(conn: sqlite3.Connection, snap: dict[str, Any]) -> None:
    exists = conn.execute("SELECT 1 FROM cash_lines WHERE id=?", (snap["line_id"],)).fetchone()
    _evict_line_no(conn, snap["line_no"], int(snap["line_id"]))
    if not exists:
        conn.execute(
            '''INSERT INTO cash_lines(
                 id, line_no, till_code, business_day, occurred_at, amount,
                 pay_method, note, created_at
               ) VALUES (?,?,?,?,?,?,?,?,?)''',
            (
                snap["line_id"],
                snap["line_no"],
                snap["till_code"],
                snap["business_day"],
                snap["occurred_at"],
                snap["amount"],
                snap["pay_method"],
                snap["note"],
                snap["created_at"],
            ),
        )
        return
    conn.execute(
        '''UPDATE cash_lines
              SET line_no=?, till_code=?, business_day=?, occurred_at=?,
                  amount=?, pay_method=?, note=?, created_at=?
            WHERE id=?''',
        (
            snap["line_no"],
            snap["till_code"],
            snap["business_day"],
            snap["occurred_at"],
            snap["amount"],
            snap["pay_method"],
            snap["note"],
            snap["created_at"],
            snap["line_id"],
        ),
    )


def reconcile_sealed_day(
    conn: sqlite3.Connection, business_day: str, *, path: str = "reconcile"
) -> dict[str, Any] | None:
    """Restore the sealed effective set and batch header to their frozen values.

    Any line added, removed, edited or moved into/out of a sealed day after the
    seal is undone. Returns a detail dict when drift was repaired, else None.
    """
    batch = current_batch(conn, business_day)
    if not batch:
        return None
    live_fp = live_line_fingerprint(conn, business_day)
    frozen_fp, trustworthy = ensure_snapshot(conn, batch, live_fp=live_fp)
    if not trustworthy:
        # The original effective set was lost before the freeze existed; there is
        # nothing to restore. Refuse to silently bless the current (drifted) set.
        return {
            "business_day": business_day,
            "batch_id": batch["id"],
            "batch_no": batch["batch_no"],
            "unrecoverable": True,
            "line_fingerprint": frozen_fp,
            "live_line_fingerprint": live_fp,
            "path": path,
        }
    snap = snapshot_rows(conn, batch["id"])
    # A line moved out of the day leaves the day's fingerprint intact but leaves
    # a row whose business_day now points elsewhere; include it in the check.
    moved_ids = [
        int(r["line_id"])
        for r in snap
        if conn.execute(
            "SELECT 1 FROM cash_lines WHERE id=? AND business_day=?", (r["line_id"], business_day)
        ).fetchone()
        is None
    ]
    frozen_total = round(sum(float(r["amount"]) for r in snap), 2)
    header_drift = (
        batch.get("header_fingerprint") is not None
        and (
            batch["header_fingerprint"]
            != header_fingerprint_of(
                batch_no=batch["batch_no"],
                business_day=business_day,
                system_total=batch["system_total"],
                line_count=batch["line_count"],
                variance=batch["variance"],
            )
            or round(float(batch["system_total"]), 2) != frozen_total
            or int(batch["line_count"]) != len(snap)
        )
    )
    if frozen_fp == live_fp and not moved_ids and not header_drift:
        return None

    keep_ids = {int(r["line_id"]) for r in snap}
    removed = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM cash_lines WHERE business_day=? ORDER BY id", (business_day,)
        ).fetchall()
        if int(r["id"]) not in keep_ids
    ]
    for r in removed:
        conn.execute("DELETE FROM cash_lines WHERE id=?", (r["id"],))
    for r in snap:
        _restore_line(conn, r)

    total = round(sum(float(r["amount"]) for r in snap), 2)
    count = len(snap)
    variance = round(float(batch["declared_cash"]) - total, 2)
    header_fp = header_fingerprint_of(
        batch_no=batch["batch_no"],
        business_day=business_day,
        system_total=total,
        line_count=count,
        variance=variance,
    )
    conn.execute(
        '''UPDATE close_batches
              SET system_total=?, line_count=?, variance=?, header_fingerprint=?
            WHERE id=?''',
        (total, count, variance, header_fp, batch["id"]),
    )
    conn.execute(
        '''UPDATE variance_rows SET variance=?, disposition=?
            WHERE batch_id=?''',
        (variance, "pending" if abs(variance) > 1e-9 else "balanced", batch["id"]),
    )
    return {
        "business_day": business_day,
        "batch_id": batch["id"],
        "batch_no": batch["batch_no"],
        "removed_line_ids": [int(r["id"]) for r in removed],
        "restored_line_count": count,
        "system_total": total,
        "line_count": count,
        "header_fingerprint": header_fp,
        "line_fingerprint": frozen_fp,
        "path": path,
    }


def reconcile_all_sealed(conn: sqlite3.Connection, *, path: str = "reconcile") -> list[dict[str, Any]]:
    days = [
        r["business_day"]
        for r in conn.execute(
            "SELECT DISTINCT business_day FROM close_batches WHERE status=?", (SEALED,)
        ).fetchall()
    ]
    repaired = []
    for day in days:
        detail = reconcile_sealed_day(conn, day, path=path)
        if not detail:
            continue
        record_conflict(
            conn,
            business_day=day,
            batch_id=detail["batch_id"],
            path=path,
            outcome="unrecoverable" if detail.get("unrecoverable") else "rolled_back",
            detail=json.dumps(detail, ensure_ascii=False),
        )
        repaired.append(detail)
    return repaired


def integrity(conn: sqlite3.Connection, business_day: str) -> dict[str, Any]:
    """Read-only fingerprint comparison for a business day (no repair)."""
    batch = current_batch(conn, business_day)
    if not batch:
        return {"business_day": business_day, "sealed": False, "consistent": True}
    snap = snapshot_rows(conn, batch["id"])
    live = conn.execute(
        "SELECT * FROM cash_lines WHERE business_day=? ORDER BY id", (business_day,)
    ).fetchall()
    live_fp = line_fingerprint_of(live)
    live_total = round(sum(float(r["amount"]) for r in live), 2)
    expected_header = header_fingerprint_of(
        batch_no=batch["batch_no"],
        business_day=business_day,
        system_total=batch["system_total"],
        line_count=batch["line_count"],
        variance=batch["variance"],
    )
    stored_header = batch.get("header_fingerprint")
    header_intact = stored_header is None or stored_header == expected_header
    header_matches_live = (
        round(float(batch["system_total"]), 2) == live_total
        and int(batch["line_count"]) == len(live)
    )

    stored_fp = batch.get("line_fingerprint")
    frozen = bool(stored_fp and snap)
    if frozen:
        consistent = (
            str(stored_fp) == live_fp
            and header_intact
            and header_matches_live
            and len(snap) == len(live)
        )
    else:
        # Pre-freeze batch: with no snapshot/fingerprint we can only say whether
        # the live set still matches the sealed header. That is the best possible
        # read-only answer; /repair explains when the original set is gone.
        consistent = header_intact and header_matches_live
    return {
        "business_day": business_day,
        "sealed": True,
        "batch_id": batch["id"],
        "batch_no": batch["batch_no"],
        "frozen": frozen,
        "line_fingerprint": stored_fp or line_fingerprint_of(snap),
        "live_line_fingerprint": live_fp,
        "header_fingerprint": stored_header or expected_header,
        "live_header_fingerprint": expected_header,
        "consistent": consistent,
        "snapshot_line_count": len(snap),
        "live_line_count": len(live),
        "batch_system_total": float(batch["system_total"]),
        "live_system_total": live_total,
    }


# ---------------------------------------------------------------------------
# Batch lifecycle
# ---------------------------------------------------------------------------

def create_batch(conn: sqlite3.Connection, **fields: Any) -> int:
    cur = conn.execute(
        '''INSERT INTO close_batches(
             batch_no, business_day, system_total, declared_cash, variance,
             line_count, status, sealed_at, note, line_fingerprint, header_fingerprint
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
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
            fields.get("line_fingerprint"),
            fields.get("header_fingerprint"),
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


# ---------------------------------------------------------------------------
# Conflict audit — every blocked bypass leaves a row
# ---------------------------------------------------------------------------

def record_conflict(
    conn: sqlite3.Connection,
    *,
    business_day: str,
    batch_id: int | None,
    path: str,
    outcome: str,
    detail: str | None = None,
) -> None:
    conn.execute(
        '''INSERT INTO write_conflicts(business_day, batch_id, path, outcome, detail, created_at)
           VALUES (?,?,?,?,?,?)''',
        (business_day, batch_id, path, outcome, detail, _now()),
    )


def list_conflicts(conn: sqlite3.Connection, business_day: str | None = None) -> list[dict[str, Any]]:
    if business_day:
        rows = conn.execute(
            "SELECT * FROM write_conflicts WHERE business_day=? ORDER BY id DESC", (business_day,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM write_conflicts ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def fence_pending_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) c FROM variance_rows WHERE disposition='pending'"
    ).fetchone()
    return int(row["c"])


def fence_current_batches(conn: sqlite3.Connection, business_day: str) -> list[dict[str, Any]]:
    """Current-sealed batches only; a voided predecessor is never reported current."""
    rows = conn.execute(
        "SELECT * FROM close_batches WHERE business_day=? AND status=? ORDER BY id DESC",
        (business_day, SEALED),
    ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["current"] = True
        out.append(item)
    return out
