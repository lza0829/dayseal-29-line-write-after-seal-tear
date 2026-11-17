from __future__ import annotations
from typing import Any
from app.db import connect
from app.repositories import closes as close_repo
from app.repositories import lines as line_repo
from app.repositories import settings as settings_repo
from app.repositories import tills as till_repo
from app import align_29 as align_mod
from app import fence_29 as fence_mod


def dashboard() -> dict[str, Any]:
    conn = connect()
    try:
        day = settings_repo.get(conn, "business_day") or ""
        total, count = line_repo.day_total(conn, day) if day else (0.0, 0)
        current = close_repo.current_batch(conn, day) if day else None
        return {
            "business_day": day,
            "till_count": len(till_repo.list_tills(conn)),
            "line_count_today": count,
            "system_total_today": total,
            "sealed_today": current is not None,
            "current_batch_no": current["batch_no"] if current else None,
            # Counters follow the same "current = sealed" rule as the write gate,
            # so a reopened (voided) batch never shows up as a sealed close.
            "close_batch_count": close_repo.count_current_batches(conn),
            "voided_batch_count": close_repo.count_voided_batches(conn),
            "sealed_day_count": close_repo.count_sealed_days(conn),
            "open_variance_count": close_repo.count_open_variances(conn),
            "conflict_count": len(close_repo.list_conflicts(conn)),
        }
    finally:
        conn.close()


def dashboard_aligned() -> dict:
    return dashboard()


def dashboard_extended() -> dict:
    base = dashboard()
    conn = connect()
    try:
        batches = close_repo.list_batches(conn)
        return align_mod.overlay_dashboard(base, batches)
    finally:
        conn.close()


def dashboard_fenced() -> dict:
    raw = dashboard()
    return fence_mod.overlay_counts(raw)


def fence_snapshot() -> dict:
    d = dashboard_fenced()
    return {
        "pending_fence": d.get("pending_fence"),
        "seal_allowed": d.get("seal_allowed"),
        "open_variance_count": d.get("open_variance_count"),
        "business_day": d.get("business_day"),
    }
