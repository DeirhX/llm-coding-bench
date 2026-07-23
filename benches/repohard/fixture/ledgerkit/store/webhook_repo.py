from __future__ import annotations
from typing import Any
from store import db

def mark_processed(webhook_id: str, meta: dict[str, Any] | None = None) -> None:
    db.put("processed_webhooks", webhook_id, meta or {"id": webhook_id})

def is_processed(webhook_id: str) -> bool:
    return db.get("processed_webhooks", webhook_id) is not None
