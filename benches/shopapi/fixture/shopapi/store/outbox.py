from __future__ import annotations

import uuid
from typing import Any

from pkg.events import Event

from . import db


def insert(event: Event) -> str:
    """Persist an event for async delivery. Returns outbox row id."""
    oid = str(uuid.uuid4())
    db.put(
        "outbox",
        oid,
        {
            "id": oid,
            "name": event.name,
            "payload": event.payload,
            "tenant_id": event.tenant_id,
            "acked": False,
            "published": False,
        },
    )
    return oid


def claim_batch(limit: int = 10) -> list[dict[str, Any]]:
    rows = [r for r in db.scan("outbox") if not r["acked"]]
    return rows[:limit]


def mark_published(outbox_id: str) -> None:
    row = db.get("outbox", outbox_id)
    if not row:
        return
    row["published"] = True
    db.put("outbox", outbox_id, row)


def ack(outbox_id: str) -> None:
    row = db.get("outbox", outbox_id)
    if not row:
        return
    row["acked"] = True
    db.put("outbox", outbox_id, row)
