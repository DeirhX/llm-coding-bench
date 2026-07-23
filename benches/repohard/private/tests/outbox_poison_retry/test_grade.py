from __future__ import annotations

from store import db, cache, outbox
from worker.outbox_worker import process_once, PoisonError


def setup_function(_=None):
    db.reset()
    cache.reset()


def test_poison_not_acked():
    eid = outbox.insert(
        {
            "id": "e-poison",
            "kind": "X",
            "tenant_id": "t1",
            "payload": {"poison": True},
        }
    )
    result = process_once()
    assert result["failed"] == 1
    row = db.get("outbox", eid)
    assert row is not None
    assert row.get("acked") is False
    assert row.get("published") is not True


def test_success_acked():
    eid = outbox.insert(
        {
            "id": "e-ok",
            "kind": "X",
            "tenant_id": "t1",
            "payload": {"poison": False},
        }
    )
    result = process_once()
    assert result["ok"] == 1
    row = db.get("outbox", eid)
    assert row["acked"] is True
    assert row["published"] is True


def test_retry_after_poison_then_fix():
    eid = outbox.insert(
        {
            "id": "e-flaky",
            "kind": "X",
            "tenant_id": "t1",
            "payload": {"poison": True},
        }
    )
    process_once()
    row = db.get("outbox", eid)
    row["payload"] = {"poison": False}
    db.put("outbox", eid, row)
    process_once()
    row2 = db.get("outbox", eid)
    assert row2["acked"] is True
