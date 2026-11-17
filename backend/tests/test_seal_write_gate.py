from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import DB_PATH
from app.main import app


DAY = "2026-11-16"
OTHER_DAY = "2026-11-15"


@pytest.fixture()
def client(seeded_template):
    """Fresh copy of the seeded database per test, exercising the real FastAPI stack."""
    for suffix in ("", "-wal", "-shm"):
        stale = DB_PATH.with_name(DB_PATH.name + suffix)
        if stale.exists():
            stale.unlink()
    DB_PATH.write_bytes(seeded_template.read_bytes())
    with TestClient(app) as c:
        c.put("/api/settings/business-day", json={"business_day": DAY})
        yield c


def raw_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def seal_day(client, day: str = DAY, declared: float | None = None) -> dict:
    preview = client.get("/api/close/preview", params={"business_day": day}).json()
    if declared is None:
        declared = preview["system_total"]
    r = client.post("/api/close/seal", json={"business_day": day, "declared_cash": declared})
    assert r.status_code == 200, r.text
    return r.json()


def batch_row(batch_id: int) -> dict:
    conn = raw_conn()
    try:
        return dict(conn.execute("SELECT * FROM close_batches WHERE id=?", (batch_id,)).fetchone())
    finally:
        conn.close()


def conflicts(client, day: str = DAY) -> list[dict]:
    r = client.get("/api/close/conflicts", params={"business_day": day})
    assert r.status_code == 200
    return r.json()


def lines_of(client, day: str = DAY) -> list[dict]:
    return client.get("/api/lines", params={"business_day": day}).json()


# ---------------------------------------------------------------------------
# Baseline: main create and patch are blocked on a sealed day
# ---------------------------------------------------------------------------

def test_create_on_sealed_day_is_rejected_and_audited(client):
    sealed = seal_day(client)
    before = lines_of(client)
    r = client.post("/api/lines", json={"till_code": "TILL-A", "amount": 5, "business_day": DAY})
    assert r.status_code == 409
    assert lines_of(client) == before
    assert batch_row(sealed["batch_id"])["system_total"] == sealed["system_total"]
    assert any(c["path"] == "create" and c["outcome"] == "rejected" for c in conflicts(client))


# ---------------------------------------------------------------------------
# Fixture 1: 支付口改金额 — patch amount/pay method on a sealed day
# ---------------------------------------------------------------------------

def test_patch_amount_on_sealed_day_is_rejected(client):
    sealed = seal_day(client)
    line = lines_of(client)[0]
    original = line["amount"]

    r = client.patch("/api/lines/%d" % line["id"], json={"amount": 9999, "pay_method": "微信"})
    assert r.status_code == 409

    after = {l["id"]: l for l in lines_of(client)}[line["id"]]
    assert after["amount"] == original
    assert after["pay_method"] == line["pay_method"]

    batch = batch_row(sealed["batch_id"])
    assert batch["system_total"] == sealed["system_total"]
    assert batch["line_count"] == sealed["line_count"]
    assert batch["header_fingerprint"] == sealed["header_fingerprint"]

    audit = conflicts(client)
    assert any(c["path"] == "patch" and c["outcome"] == "rejected" for c in audit)


def test_delete_on_sealed_day_is_rejected(client):
    sealed = seal_day(client)
    line = lines_of(client)[0]
    r = client.delete("/api/lines/%d" % line["id"])
    assert r.status_code == 409
    assert any(l["id"] == line["id"] for l in lines_of(client))
    assert batch_row(sealed["batch_id"])["line_count"] == sealed["line_count"]


# ---------------------------------------------------------------------------
# Fixture 2: 导入进已封日 — bulk import into a sealed day
# ---------------------------------------------------------------------------

def test_import_into_sealed_day_writes_nothing(client):
    sealed = seal_day(client)
    before = lines_of(client)
    payload = {
        "lines": [
            {"till_code": "TILL-A", "amount": 10, "business_day": DAY},
            {"till_code": "TILL-B", "amount": 20, "business_day": OTHER_DAY},
        ]
    }
    r = client.post("/api/lines/import", json=payload)
    assert r.status_code == 409
    # Nothing is written for any day, including the unsealed one in the same batch.
    assert lines_of(client) == before
    assert len(lines_of(client, OTHER_DAY)) == 1
    assert batch_row(sealed["batch_id"])["line_count"] == sealed["line_count"]
    assert any(c["path"] == "import" for c in conflicts(client))


def test_import_into_unsealed_day_succeeds(client):
    before = len(lines_of(client, OTHER_DAY))
    r = client.post(
        "/api/lines/import",
        json={"lines": [{"till_code": "TILL-A", "amount": 10, "business_day": OTHER_DAY}]},
    )
    assert r.status_code == 200, r.text
    assert len(lines_of(client, OTHER_DAY)) == before + 1


# ---------------------------------------------------------------------------
# Fixture 3: 冲正旁路改已封 — reversal against a sealed day
# ---------------------------------------------------------------------------

def test_reverse_on_sealed_day_is_rejected(client):
    sealed = seal_day(client)
    line = lines_of(client)[0]
    before = lines_of(client)
    r = client.post("/api/lines/%d/reverse" % line["id"], json={"reason": "冲正"})
    assert r.status_code == 409
    assert lines_of(client) == before
    batch = batch_row(sealed["batch_id"])
    assert batch["system_total"] == sealed["system_total"]
    assert any(c["path"] == "reverse" for c in conflicts(client))


def test_reverse_on_unsealed_day_posts_negative_line(client):
    line = lines_of(client)[0]
    r = client.post("/api/lines/%d/reverse" % line["id"], json={"reason": "冲正"})
    assert r.status_code == 200, r.text
    assert r.json()["reversal"]["amount"] == -abs(line["amount"])


# ---------------------------------------------------------------------------
# Fixture 4: 封后改日 — moving a line's business day
# ---------------------------------------------------------------------------

def test_move_line_into_sealed_day_is_rejected(client):
    sealed = seal_day(client)
    other_line = lines_of(client, OTHER_DAY)[0]
    r = client.patch("/api/lines/%d/day" % other_line["id"], json={"business_day": DAY})
    assert r.status_code == 409
    assert {l["id"]: l for l in lines_of(client, OTHER_DAY)}[other_line["id"]]["business_day"] == OTHER_DAY
    assert batch_row(sealed["batch_id"])["line_count"] == sealed["line_count"]
    assert any(c["path"] == "move_day" for c in conflicts(client))


def test_move_line_out_of_sealed_day_is_rejected(client):
    sealed = seal_day(client)
    sealed_line = lines_of(client)[0]
    r = client.patch("/api/lines/%d/day" % sealed_line["id"], json={"business_day": OTHER_DAY})
    assert r.status_code == 409
    assert any(l["id"] == sealed_line["id"] for l in lines_of(client, DAY))
    assert batch_row(sealed["batch_id"])["line_count"] == sealed["line_count"]


def test_move_line_between_unsealed_days_succeeds(client):
    other_line = lines_of(client, OTHER_DAY)[0]
    r = client.patch("/api/lines/%d/day" % other_line["id"], json={"business_day": "2026-11-14"})
    assert r.status_code == 200, r.text
    assert r.json()["business_day"] == "2026-11-14"


# ---------------------------------------------------------------------------
# 解款附属写入 — pickup side channel
# ---------------------------------------------------------------------------

def test_pickup_on_sealed_day_is_rejected(client):
    seal_day(client)
    r = client.post(
        "/api/lines/pickups", json={"till_code": "TILL-A", "amount": 50, "business_day": DAY}
    )
    assert r.status_code == 409
    assert client.get("/api/lines/pickups", params={"business_day": DAY}).json() == []
    assert any(c["path"] == "pickup" for c in conflicts(client))


def test_pickup_on_unsealed_day_succeeds(client):
    r = client.post(
        "/api/lines/pickups", json={"till_code": "TILL-A", "amount": 50, "business_day": OTHER_DAY}
    )
    assert r.status_code == 200, r.text
    assert len(client.get("/api/lines/pickups", params={"business_day": OTHER_DAY}).json()) == 1


# ---------------------------------------------------------------------------
# Drift repair: a write that bypassed the API (or a pre-fix leak) is rolled back
# ---------------------------------------------------------------------------

def test_leaked_post_seal_write_is_rolled_back_and_header_restored(client):
    sealed = seal_day(client)
    victim = lines_of(client)[0]

    # Simulate a write that slipped past the gate (raw DB / concurrent bypass).
    conn = raw_conn()
    try:
        conn.execute("UPDATE cash_lines SET amount = 123456 WHERE id=?", (victim["id"],))
        conn.execute(
            "INSERT INTO cash_lines(line_no, till_code, business_day, occurred_at, amount, pay_method, note, created_at)"
            " VALUES ('CL-HACK','TILL-A',?,?,1.0,'现金',NULL,?)",
            (DAY, "2026-11-16T23:00:00Z", "2026-11-16T23:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()

    # The next gated write is rejected *and* repairs the sealed effective set.
    r = client.post("/api/lines", json={"till_code": "TILL-A", "amount": 5, "business_day": DAY})
    assert r.status_code == 409
    assert "回滚" in r.json()["detail"] or "已回滚" in r.json()["detail"]

    restored = {l["id"]: l for l in lines_of(client)}[victim["id"]]
    assert restored["amount"] == victim["amount"]
    assert not any(l["line_no"] == "CL-HACK" for l in lines_of(client))

    batch = batch_row(sealed["batch_id"])
    assert batch["system_total"] == sealed["system_total"]
    assert batch["line_count"] == sealed["line_count"]
    assert batch["header_fingerprint"] == sealed["header_fingerprint"]

    audit = conflicts(client)
    assert any(c["path"] == "create" and c["outcome"] == "rolled_back" for c in audit)


def test_integrity_reports_drift_without_repair(client):
    seal_day(client)
    conn = raw_conn()
    try:
        conn.execute("UPDATE cash_lines SET amount = 1 WHERE business_day=?", (DAY,))
        conn.commit()
    finally:
        conn.close()

    report = client.get("/api/close/integrity", params={"business_day": DAY}).json()
    assert report["sealed"] is True
    assert report["consistent"] is False

    repaired = client.post("/api/close/repair", json={"business_day": DAY}).json()
    assert repaired["count"] == 1
    report = client.get("/api/close/integrity", params={"business_day": DAY}).json()
    assert report["consistent"] is True


def test_repair_restores_line_moved_out_of_sealed_day(client):
    seal_day(client)
    line = lines_of(client)[0]
    conn = raw_conn()
    try:
        conn.execute("UPDATE cash_lines SET business_day=? WHERE id=?", (OTHER_DAY, line["id"]))
        conn.commit()
    finally:
        conn.close()

    r = client.post("/api/close/repair", json={"business_day": DAY})
    assert r.status_code == 200
    assert any(l["id"] == line["id"] for l in lines_of(client, DAY))
    assert all(l["id"] != line["id"] for l in lines_of(client, OTHER_DAY))


# ---------------------------------------------------------------------------
# Seal / reopen invariants survive the new freeze
# ---------------------------------------------------------------------------

def test_seal_freezes_snapshot_and_fingerprints(client):
    sealed = seal_day(client)
    batch = batch_row(sealed["batch_id"])
    assert batch["line_fingerprint"] == sealed["line_fingerprint"]
    assert batch["header_fingerprint"] == sealed["header_fingerprint"]
    assert batch["line_fingerprint"]
    assert batch["header_fingerprint"]

    report = client.get("/api/close/integrity", params={"business_day": DAY}).json()
    assert report["consistent"] is True
    assert report["snapshot_line_count"] == report["live_line_count"] == sealed["line_count"]


def test_reopen_unlocks_then_reseal_supersedes(client):
    first = seal_day(client)
    r = client.post("/api/close/reopen", json={"business_day": DAY, "reason": "补录"})
    assert r.status_code == 200, r.text

    # Day writable again; side channels open.
    add = client.post("/api/lines", json={"till_code": "TILL-A", "amount": 7, "business_day": DAY})
    assert add.status_code == 200, add.text

    second = seal_day(client)
    assert second["batch_no"] != first["batch_no"]
    assert first["batch_id"] in second["superseded_batch_ids"]

    old = batch_row(first["batch_id"])
    assert old["status"] == "voided"
    assert old["superseded_by_batch_id"] == second["batch_id"]
    # Historic totals are never rewritten by reopen.
    assert old["system_total"] == first["system_total"]

    # Only the new batch is current; counter agrees.
    dash = client.get("/api/dashboard").json()
    assert dash["sealed_today"] is True
    assert dash["current_batch_no"] == second["batch_no"]


def test_all_write_paths_share_one_gate(client):
    seal_day(client)
    matrix = client.get("/api/lines/write-paths", params={"business_day": DAY}).json()
    sealed_paths = ["create", "patch", "remove", "import", "reverse", "move_day", "pickup"]
    for path in sealed_paths:
        assert matrix[path] is False, (path, matrix[path])
    assert matrix["open"] is False

    client.put("/api/settings/business-day", json={"business_day": OTHER_DAY})
    matrix = client.get("/api/lines/write-paths", params={"business_day": OTHER_DAY}).json()
    for path in sealed_paths:
        assert matrix[path] is True, (path, matrix[path])


def test_legacy_unfrozen_batch_with_drift_is_reported_not_blessed(client):
    """A pre-freeze sealed batch whose live set no longer matches its header must
    not have the drifted set adopted as the frozen truth."""
    sealed = seal_day(client)
    conn = raw_conn()
    try:
        # Strip the freeze metadata and add a post-seal line: the original set
        # can no longer be reconstructed.
        conn.execute("DELETE FROM sealed_line_snapshot WHERE batch_id=?", (sealed["batch_id"],))
        conn.execute(
            "UPDATE close_batches SET line_fingerprint=NULL, header_fingerprint=NULL WHERE id=?",
            (sealed["batch_id"],),
        )
        conn.execute(
            "INSERT INTO cash_lines(line_no,till_code,business_day,occurred_at,amount,pay_method,note,created_at)"
            " VALUES ('CL-LEAK','TILL-A',?,?,99,'现金',NULL,?)",
            (DAY, "2026-11-16T23:00:00Z", "2026-11-16T23:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()

    report = client.get("/api/close/integrity", params={"business_day": DAY}).json()
    assert report["frozen"] is False
    assert report["consistent"] is False

    r = client.post("/api/close/repair", json={"business_day": DAY}).json()
    assert r["repaired"][0]["unrecoverable"] is True
    audit = client.get("/api/close/conflicts", params={"business_day": DAY}).json()
    assert any(c["outcome"] == "unrecoverable" for c in audit)
    # The leaked line is still present: nothing was silently frozen over it.
    assert any(l["line_no"] == "CL-LEAK" for l in lines_of(client))


def test_legacy_unfrozen_batch_matching_header_is_adopted(client):
    """Without metadata but with a live set that still matches the header, the
    repair may safely adopt it as the frozen set going forward."""
    sealed = seal_day(client)
    conn = raw_conn()
    try:
        conn.execute("DELETE FROM sealed_line_snapshot WHERE batch_id=?", (sealed["batch_id"],))
        conn.execute(
            "UPDATE close_batches SET line_fingerprint=NULL, header_fingerprint=NULL WHERE id=?",
            (sealed["batch_id"],),
        )
        conn.commit()
    finally:
        conn.close()

    r = client.post("/api/close/repair", json={"business_day": DAY}).json()
    assert r["count"] == 0  # nothing needed repair; snapshot adopted
    report = client.get("/api/close/integrity", params={"business_day": DAY}).json()
    assert report["frozen"] is True
    assert report["consistent"] is True


def test_dashboard_counters_exclude_voided_batches(client):
    seal_day(client)
    client.post("/api/close/reopen", json={"business_day": DAY, "reason": "补录"})
    dash = client.get("/api/dashboard").json()
    assert dash["sealed_today"] is False
    assert dash["close_batch_count"] == 0
    assert dash["voided_batch_count"] == 1
