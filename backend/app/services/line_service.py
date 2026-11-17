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


def _resolve_day(conn, body: dict[str, Any]) -> str:
    day = (body.get("business_day") or settings_repo.get(conn, "business_day") or "").strip()
    if not day:
        raise HTTPException(400, "未设置营业日")
    return day


def list_lines(business_day: str | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return line_repo.list_lines(conn, business_day)
    finally:
        conn.close()


def create_line(body: dict[str, Any]) -> dict[str, Any]:
    conn = connect(immediate=True)
    try:
        day = _resolve_day(conn, body)
        write_paths.assert_writable(
            conn, close_repo, day, write_paths.CREATE, detail={"till_code": body.get("till_code")}
        )
        created = _now()
        lid = line_repo.insert_line(
            conn,
            line_no=line_repo.next_line_no(conn),
            till_code=body["till_code"],
            business_day=day,
            occurred_at=body.get("occurred_at") or created,
            amount=float(body["amount"]),
            pay_method=body.get("pay_method") or "现金",
            note=body.get("note"),
            created_at=created,
        )
        conn.commit()
        return line_repo.get_line(conn, lid)  # type: ignore
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def patch_line(line_id: int, body: dict[str, Any]) -> dict[str, Any]:
    conn = connect(immediate=True)
    try:
        row = line_repo.get_line(conn, line_id)
        if not row:
            raise HTTPException(404, "流水不存在")
        write_paths.assert_writable(
            conn,
            close_repo,
            row["business_day"],
            write_paths.PATCH,
            detail={"line_id": line_id, "fields": sorted(body.keys())},
        )
        line_repo.update_line(
            conn,
            line_id,
            amount=float(body.get("amount", row["amount"])),
            pay_method=body.get("pay_method", row["pay_method"]),
            note=body.get("note", row["note"]),
        )
        conn.commit()
        return line_repo.get_line(conn, line_id)  # type: ignore
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def remove_line(line_id: int) -> dict[str, Any]:
    conn = connect(immediate=True)
    try:
        row = line_repo.get_line(conn, line_id)
        if not row:
            raise HTTPException(404, "流水不存在")
        write_paths.assert_writable(
            conn, close_repo, row["business_day"], write_paths.REMOVE, detail={"line_id": line_id}
        )
        line_repo.delete_line(conn, line_id)
        conn.commit()
        return {"deleted": True, "id": line_id}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def import_lines(body: dict[str, Any]) -> dict[str, Any]:
    """Bulk补录. Lines may target several days; every touched sealed day blocks the batch.

    The whole import is one transaction: if any target day is sealed, nothing is
    written for any day, and the conflict is audited against that sealed day.
    """
    rows = body.get("lines") or []
    if not rows:
        raise HTTPException(400, "导入内容为空")
    conn = connect(immediate=True)
    try:
        default_day = (body.get("business_day") or "").strip()
        resolved = []
        for r in rows:
            day = str(r.get("business_day") or default_day or "").strip()
            if not day:
                day = (settings_repo.get(conn, "business_day") or "").strip()
            if not day:
                raise HTTPException(400, "导入行缺少营业日")
            resolved.append((day, r))
        write_paths.assert_days_writable(
            conn,
            close_repo,
            [d for d, _ in resolved],
            write_paths.IMPORT,
            detail={"line_count": len(resolved)},
        )
        created_at = _now()
        created = []
        for day, r in resolved:
            lid = line_repo.insert_line(
                conn,
                line_no=line_repo.next_line_no(conn),
                till_code=r["till_code"],
                business_day=day,
                occurred_at=r.get("occurred_at") or created_at,
                amount=float(r["amount"]),
                pay_method=r.get("pay_method") or "现金",
                note=r.get("note"),
                created_at=created_at,
            )
            created.append(line_repo.get_line(conn, lid))
        conn.commit()
        return {"imported": len(created), "lines": created}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reverse_line(line_id: int, body: dict[str, Any]) -> dict[str, Any]:
    """冲正: post a negative counter-line against the original line's day."""
    conn = connect(immediate=True)
    try:
        row = line_repo.get_line(conn, line_id)
        if not row:
            raise HTTPException(404, "流水不存在")
        day = row["business_day"]
        write_paths.assert_writable(
            conn,
            close_repo,
            day,
            write_paths.REVERSE,
            detail={"line_id": line_id, "amount": row["amount"]},
        )
        created = _now()
        amount = round(-abs(float(row["amount"])), 2)
        note = body.get("reason") or f"冲正 {row['line_no']}"
        lid = line_repo.insert_line(
            conn,
            line_no=line_repo.next_line_no(conn),
            till_code=row["till_code"],
            business_day=day,
            occurred_at=body.get("occurred_at") or created,
            amount=amount,
            pay_method=row["pay_method"],
            note=note,
            created_at=created,
        )
        conn.commit()
        return {"reversed": line_id, "reversal": line_repo.get_line(conn, lid)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def move_line_day(line_id: int, body: dict[str, Any]) -> dict[str, Any]:
    """改归属日: a move touches both the source and the destination partition."""
    target = str(body.get("business_day") or "").strip()
    if not target:
        raise HTTPException(400, "未指定目标营业日")
    conn = connect(immediate=True)
    try:
        row = line_repo.get_line(conn, line_id)
        if not row:
            raise HTTPException(404, "流水不存在")
        source = row["business_day"]
        if source == target:
            return line_repo.get_line(conn, line_id)  # type: ignore
        write_paths.assert_days_writable(
            conn,
            close_repo,
            [source, target],
            write_paths.MOVE_DAY,
            detail={"line_id": line_id, "from": source, "to": target},
        )
        line_repo.move_line_day(conn, line_id, target)
        conn.commit()
        return line_repo.get_line(conn, line_id)  # type: ignore
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_pickup(body: dict[str, Any]) -> dict[str, Any]:
    """解款: ancillary cash write that must obey the same seal gate."""
    conn = connect(immediate=True)
    try:
        day = _resolve_day(conn, body)
        write_paths.assert_writable(
            conn,
            close_repo,
            day,
            write_paths.PICKUP,
            detail={"till_code": body.get("till_code"), "amount": body.get("amount")},
        )
        pid = line_repo.insert_pickup(
            conn,
            business_day=day,
            till_code=str(body.get("till_code") or ""),
            amount=float(body["amount"]),
            note=body.get("note"),
            created_at=_now(),
        )
        conn.commit()
        return line_repo.get_pickup(conn, pid)  # type: ignore
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_pickups(business_day: str | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return line_repo.list_pickups(conn, business_day)
    finally:
        conn.close()


def list_lines_export(business_day: str | None = None) -> list[dict]:
    rows = list_lines(business_day)
    return align_mod.filter_lines_for_export(rows)


def write_path_matrix(business_day: str | None = None) -> dict:
    """Per-day view of every entry point; all follow the same seal rule."""
    conn = connect()
    try:
        day = business_day or settings_repo.get(conn, "business_day") or ""
        sealed_days = [day] if day and close_repo.is_sealed(conn, day) else []
        matrix = write_paths.gate_matrix(sealed_days)
        matrix["business_day"] = day
        matrix["paths"] = fence_mod.bypass_matrix(sealed_days)
        return matrix
    finally:
        conn.close()


def sealed_side_channel(day: str) -> dict:
    conn = connect()
    try:
        sealed = close_repo.is_sealed(conn, day)
        return {
            "day": day,
            "sealed": sealed,
            "paths": write_paths.gate_matrix([day] if sealed else []),
            "any_open": not sealed,
        }
    finally:
        conn.close()
