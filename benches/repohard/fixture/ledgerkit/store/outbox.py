from __future__ import annotations

from typing import Any
import uuid

from store import db


def insert(event: dict[str, Any]) -> str:
    eid = event.get("id") or str(uuid.uuid4())
    row = {**event, "id": eid, "acked": False, "published": False}
    db.put("outbox", eid, row)
    return eid


def claim_batch(limit: int = 32) -> list[dict[str, Any]]:
    rows = [r for r in db.list_all("outbox") if not r.get("acked")]
    return rows[:limit]


def ack(row_id: str) -> None:
    row = db.get("outbox", row_id)
    if row:
        row = {**row, "acked": True}
        db.put("outbox", row_id, row)


def mark_published(row_id: str) -> None:
    row = db.get("outbox", row_id)
    if row:
        row = {**row, "published": True}
        db.put("outbox", row_id, row)
