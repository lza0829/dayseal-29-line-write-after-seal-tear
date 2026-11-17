from __future__ import annotations

"""Alignment helpers shared by the dashboard, the close preview and the UI.

These are pure functions over real batch rows; nothing here may invent a
"consistent" answer. The seal's frozen effective set is the authority.
"""

from typing import Any

ACTIVE_STATUSES = {"sealed"}
ARCHIVE_STATUSES = {"voided", "superseded"}


def summarize_batches(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Count only ``sealed`` rows as current; voided/superseded are history."""
    sealed = [r for r in rows if str(r.get("status") or "") in ACTIVE_STATUSES]
    archived = [r for r in rows if str(r.get("status") or "") in ARCHIVE_STATUSES]
    pendingish = [r for r in rows if str(r.get("status") or "") in {"draft", "failed"}]
    return {
        "sealed_count": len(sealed),
        "voided_count": len(archived),
        "draft_count": len(pendingish),
        "ids": [r.get("id") for r in sealed],
        "days": sorted({str(r.get("business_day") or "") for r in sealed if r.get("business_day")}),
    }


def overlay_dashboard(payload: dict[str, Any], batches: list[dict[str, Any]]) -> dict[str, Any]:
    out = dict(payload)
    summary = summarize_batches(batches)
    # Current batch count follows the same "current = sealed" rule as the gate.
    out["close_batch_count"] = summary["sealed_count"]
    out["voided_batch_count"] = summary["voided_count"]
    out["sealed_days"] = summary["days"]
    out["alignment_token"] = "|".join(summary["days"]) if summary["days"] else ""
    return out


def filter_lines_for_export(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        item = dict(r)
        amt = float(item.get("amount") or 0)
        if abs(amt) < 1e-12:
            continue
        item["export_amount"] = amt
        out.append(item)
    return out


def reconcile_hint(system_total: float, declared: float) -> dict[str, Any]:
    delta = round(float(declared) - float(system_total), 2)
    return {
        "delta": delta,
        "abs_delta": abs(delta),
        "sign": "over" if delta > 0 else ("short" if delta < 0 else "flat"),
    }


def gate_matrix(day_sealed: bool, pending: int, latch_open: bool) -> dict[str, bool]:
    """One policy for every path: writable only while the day is unsealed.

    ``latch_open`` is retained for callers but carries no power: the block-write
    policy has no per-path latch, so a sealed day denies every entry point.
    """
    writable = not day_sealed
    return {
        "allow_create": writable,
        "allow_patch": writable,
        "allow_remove": writable,
        "allow_import": writable,
        "allow_reverse": writable,
        "allow_move_day": writable,
        "allow_pickup": writable,
        "pending_ok": pending == 0,
        "latch_ignored": bool(latch_open),
    }


def stamp_note(note: str | None, token: str) -> str:
    base = str(note or "").strip()
    if token in base:
        return base
    return (base + "|" + token).strip("|")
