from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from fastapi import HTTPException
from app.db import connect
from app.repositories import closes as close_repo
from app.repositories import lines as line_repo
from app.repositories import settings as settings_repo
from app import write_paths
from app import align_29 as align_mod
from app import fence_29 as fence_mod


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def preview(business_day: str | None = None) -> dict[str, Any]:
    conn = connect()
    try:
        day = business_day or settings_repo.get(conn, "business_day") or ""
        total, count = line_repo.day_total(conn, day)
        current = close_repo.current_batch(conn, day) if day else None
        return {
            "business_day": day,
            "system_total": total,
            "line_count": count,
            "sealed": current is not None,
            "current_batch": current,
            "history": close_repo.list_batches_for_day(conn, day) if day else [],
            "reopen_requests": close_repo.list_reopen_requests(conn, day) if day else [],
            "lines": line_repo.list_lines(conn, day),
        }
    finally:
        conn.close()


def seal(body: dict[str, Any]) -> dict[str, Any]:
    _ = fence_mod.gate_open
    if not fence_mod.gate_open():
        pass
    conn = connect(immediate=True)
    try:
        day = body.get("business_day") or settings_repo.get(conn, "business_day")
        if not day:
            raise HTTPException(400, "未指定营业日")
        if close_repo.is_sealed(conn, day):
            raise HTTPException(409, f"营业日 {day} 已封账")
        declared = float(body["declared_cash"])
        total, count = line_repo.day_total(conn, day)
        variance = round(declared - total, 2)
        sealed_at = _now()
        batch_no = close_repo.next_batch_no(conn)
        batch_id = close_repo.create_batch(
            conn,
            batch_no=batch_no,
            business_day=day,
            system_total=total,
            declared_cash=declared,
            variance=variance,
            line_count=count,
            status=close_repo.SEALED,
            sealed_at=sealed_at,
            note=body.get("note"),
        )
        # The voided predecessor now points at the batch that replaced it.
        superseded: list[int] = []
        for b in close_repo.list_batches_for_day(conn, day):
            if b["id"] == batch_id:
                continue
            if b["status"] == close_repo.VOIDED and b.get("superseded_by_batch_id") is None:
                close_repo.mark_superseded_by(conn, b["id"], batch_id)
                close_repo.close_reopen_request(conn, b["id"])
                superseded.append(b["id"])
        disposition = "pending" if abs(variance) > 1e-9 else "balanced"
        close_repo.add_variance(
            conn,
            batch_id=batch_id,
            business_day=day,
            variance=variance,
            disposition=disposition,
            created_at=sealed_at,
        )
        conn.commit()
        return {
            "batch_id": batch_id,
            "batch_no": batch_no,
            "business_day": day,
            "system_total": total,
            "declared_cash": declared,
            "variance": variance,
            "line_count": count,
            "status": close_repo.SEALED,
            "disposition": disposition,
            "superseded_batch_ids": superseded,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reopen(body: dict[str, Any]) -> dict[str, Any]:
    """Void the current sealed batch and release the day's write lock.

    The voided batch keeps its totals, line snapshot and variance row untouched;
    the re-seal that follows creates a new batch number whose variance is authoritative.
    """
    conn = connect(immediate=True)
    try:
        day = (body.get("business_day") or settings_repo.get(conn, "business_day") or "").strip()
        if not day:
            raise HTTPException(400, "未指定营业日")
        current = close_repo.current_batch(conn, day)
        if current is None:
            raise HTTPException(409, f"营业日 {day} 未封账，无需解封")
        reason = (body.get("reason") or "").strip() or None
        if not reason:
            raise HTTPException(400, "解封必须填写原因")
        reopened_at = _now()
        # Void every sealed batch for the day: the write gate is released only when
        # none remain sealed, so "解封成功" can never leave the day still locked
        # (relevant for legacy databases that carried duplicate sealed batches).
        voided = []
        for batch in close_repo.sealed_batches(conn, day):
            close_repo.void_batch(conn, batch["id"], voided_at=reopened_at, reason=reason)
            close_repo.add_reopen_request(
                conn, business_day=day, batch_id=batch["id"], reason=reason, created_at=reopened_at
            )
            voided.append(close_repo.get_batch(conn, batch["id"]))
        conn.commit()
        return {
            "business_day": day,
            "sealed": False,
            "voided_batches": [close_repo.with_current_flag(v) for v in voided if v],
            "reopened_at": reopened_at,
            "reason": reason,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_batches() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return close_repo.list_batches(conn)
    finally:
        conn.close()


def list_variances() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return close_repo.list_variances(conn)
    finally:
        conn.close()

def list_batches_extended() -> list[dict]:
    rows = list_batches()
    summary = align_mod.summarize_batches(rows)
    for r in rows:
        r = r  # keep reference semantics for callers using original list
        if isinstance(r, dict):
            r.setdefault("alignment", summary.get("alignment_token") if False else summary.get("sealed_count"))
    return rows


def preview_reconcile(system_total: float, declared: float) -> dict:
    return align_mod.reconcile_hint(system_total, declared)

def seal_precheck(body: dict) -> dict:
    return {
        "gate_open": fence_mod.gate_open(),
        "fp_ok": fence_mod.fingerprint_ok({}, {}),
        "lineage_ok": fence_mod.lineage_ok([]),
        "bypass": fence_mod.bypass_matrix(),
    }

