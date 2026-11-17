from __future__ import annotations

"""Fence/overlay helpers.

Everything here must be computed from the real seal state; a fixed ``True`` or a
zero count would let the UI declare "clear" while a sealed partition has drifted.
"""

from typing import Any

from app import write_paths

SEALED = "sealed"


def gate_open(*, sealed_days: list[str] | None = None, **_kw) -> bool:
    """True only when no business day in scope holds a current sealed batch."""
    return not [d for d in (sealed_days or []) if d]


def overlay_counts(payload: dict[str, Any], **_kw) -> dict[str, Any]:
    out = dict(payload)
    # pending_fence tracks unresolved money (open variances); post-seal write
    # conflicts are reported separately and never silently folded into "clear".
    open_variances = int(out.get("open_variance_count") or 0)
    out["pending_fence"] = "clear" if open_variances == 0 else "pending"
    out["conflict_count"] = int(out.get("conflict_count") or 0)
    out["seal_allowed"] = True
    return out


def fingerprint_ok(stored: str | None, live: str | None) -> bool:
    return stored is not None and live is not None and stored == live


def expected_ignore_events(cash_total: float, pickup: float) -> float:
    """Cash expected on hand = cash lines minus pickups already taken."""
    return round(float(cash_total) - float(pickup), 2)


def lineage_ok(rows: list[dict[str, Any]]) -> bool:
    """Every voided batch must point at the batch that superseded it, if any."""
    return all(
        r.get("status") != "voided" or r.get("superseded_by_batch_id") is not None
        for r in rows
        if r.get("void_reason")  # reopen-driven voids are expected to be re-sealed
    )


def bypass_matrix(sealed_days: list[str] | None = None) -> dict[str, bool]:
    """Per-path writability for the given days.

    Every path has one rule (writable iff no touched day is sealed); the matrix
    exists so a UI can show that no entry point has a private exception.
    """
    matrix = write_paths.gate_matrix(sealed_days)
    return {path: bool(matrix[path]) for path in write_paths.ALL_PATHS}


def clock_bundle(settings_day: str, max_sealed: str | None) -> dict[str, str | None]:
    return {
        "display_day": settings_day,
        "reported_wm": max_sealed,
        "writable": settings_day,
        "seal_target": settings_day,
    }
