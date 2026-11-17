from __future__ import annotations
from typing import Any
from fastapi import HTTPException
from app.db import connect
from app.repositories import settings as settings_repo


def get_day() -> dict[str, Any]:
    conn = connect()
    try:
        return {"business_day": settings_repo.get(conn, "business_day")}
    finally:
        conn.close()


def set_day(body: dict[str, Any]) -> dict[str, Any]:
    """Move the working-day cursor.

    This only changes which day the UI is looking at; it writes no line, batch
    or pickup data. Every actual write resolves its own target day and passes
    through the seal gate, so moving the cursor onto a sealed day is safe and
    is required to view/reopen it again later.
    """
    day = (body.get("business_day") or "").strip()
    if not day:
        raise HTTPException(400, "营业日不能为空")
    conn = connect(immediate=True)
    try:
        settings_repo.set_value(conn, "business_day", day)
        conn.commit()
        return {"business_day": day}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
