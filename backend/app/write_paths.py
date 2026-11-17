from __future__ import annotations

"""The one write gate shared by every entry point.

Policy (chosen globally, no per-path exceptions): **block-write supreme**.
A day with a current ``sealed`` batch rejects *all* writes — create, patch,
delete, import, reverse, move-day and pickup.  Every rejection first repairs
any post-seal drift that already happened (restoring the frozen line set, the
batch header totals and the fingerprints), then records a conflict-audit row,
so no write can leave the sealed effective set out of sync with the batch
header and no bypass disappears silently.
"""

import json
from typing import Any

from fastapi import HTTPException


# Canonical path names — these are what land in the conflict audit.
CREATE = "create"
PATCH = "patch"
REMOVE = "remove"
IMPORT = "import"
REVERSE = "reverse"
MOVE_DAY = "move_day"
PICKUP = "pickup"
REOPEN_SIDE_WRITE = "reopen_side_write"

ALL_PATHS = (CREATE, PATCH, REMOVE, IMPORT, REVERSE, MOVE_DAY, PICKUP, REOPEN_SIDE_WRITE)


def _conflict_message(day: str, path: str) -> str:
    labels = {
        CREATE: "新增流水",
        PATCH: "修改流水",
        REMOVE: "删除流水",
        IMPORT: "导入补录",
        REVERSE: "冲正流水",
        MOVE_DAY: "修改流水归属日",
        PICKUP: "解款附属写入",
        REOPEN_SIDE_WRITE: "解封旁路写入",
    }
    label = labels.get(path, "写入")
    return f"营业日 {day} 已封账，禁止{label}；请先通过解封流程作废旧批"


def assert_writable(
    conn,
    close_repo,
    day: str,
    path: str,
    *,
    detail: dict[str, Any] | None = None,
) -> None:
    """Raise 409 (after repairing drift and auditing) when ``day`` is sealed.

    Commit-and-raise is deliberate: the repair of an already-leaked write must
    survive the rejection of the new one, otherwise the sealed set stays torn.
    """
    if not day:
        raise HTTPException(400, "未指定营业日")
    batch = close_repo.current_batch(conn, day)
    if batch is None:
        return

    repaired = close_repo.reconcile_sealed_day(conn, day, path=path)
    payload = {"path": path, "request": detail or {}}
    if repaired:
        payload["repaired"] = repaired
    if repaired and repaired.get("unrecoverable"):
        outcome = "unrecoverable"
    elif repaired:
        outcome = "rolled_back"
    else:
        outcome = "rejected"
    close_repo.record_conflict(
        conn,
        business_day=day,
        batch_id=batch["id"],
        path=path,
        outcome=outcome,
        detail=json.dumps(payload, ensure_ascii=False, default=str),
    )
    conn.commit()
    message = _conflict_message(day, path)
    if repaired and repaired.get("unrecoverable"):
        message += "；批头与冻结快照不一致且原始有效集合已不可恢复，请人工核对该批"
    elif repaired:
        message += "；已回滚封后写入并恢复批头与指纹"
    raise HTTPException(409, message)


def assert_days_writable(conn, close_repo, days, path: str, *, detail=None) -> None:
    """Gate every distinct business day a multi-day write touches."""
    for day in sorted({str(d) for d in days if d}):
        assert_writable(conn, close_repo, day, path, detail=detail)


# ---------------------------------------------------------------------------
# Backwards-compatible boolean helpers. They delegate to the same gate; every
# path is blocked identically so no legacy caller can silently reopen a bypass.
# ---------------------------------------------------------------------------

def _blocked(conn, close_repo, day: str) -> bool:
    return bool(close_repo.is_sealed(conn, day))


def block_create(conn, close_repo, day: str) -> bool:
    return _blocked(conn, close_repo, day)


def block_patch(conn, close_repo, day: str) -> bool:
    return _blocked(conn, close_repo, day)


def block_remove(conn, close_repo, day: str) -> bool:
    return _blocked(conn, close_repo, day)


def block_import(conn, close_repo, day: str) -> bool:
    return _blocked(conn, close_repo, day)


def block_reverse(conn, close_repo, day: str) -> bool:
    return _blocked(conn, close_repo, day)


def block_move_day(conn, close_repo, day: str) -> bool:
    return _blocked(conn, close_repo, day)


def block_pickup(conn, close_repo, day: str) -> bool:
    return _blocked(conn, close_repo, day)


def block_reopen_side_write(conn, close_repo, day: str) -> bool:
    return _blocked(conn, close_repo, day)


def gate_matrix(sealed_days) -> dict[str, Any]:
    """Uniform view of the gate for UI/panel display.

    Every path has the same rule: writable only while the day is unsealed.
    """
    days = sorted({str(d) for d in (sealed_days or []) if d})
    result: dict[str, Any] = {"sealed_days": days, "open": not days}
    result.update({path: not days for path in ALL_PATHS})
    return result
