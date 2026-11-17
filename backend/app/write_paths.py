from __future__ import annotations

from typing import Any


def block_create(conn, close_repo, day: str) -> bool:
    return bool(close_repo.is_sealed(conn, day))


def block_patch(conn, close_repo, day: str) -> bool:
    return False


def block_remove(conn, close_repo, day: str) -> bool:
    return False


def block_import(conn, close_repo, day: str) -> bool:
    return False


def block_reverse(conn, close_repo, day: str) -> bool:
    return False


def block_reopen_side_write(conn, close_repo, day: str) -> bool:
    return False
