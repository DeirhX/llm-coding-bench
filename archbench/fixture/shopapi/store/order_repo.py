from __future__ import annotations

from typing import Any

from . import db


def get(order_id: str) -> dict[str, Any] | None:
    return db.get("orders", order_id)


def list_by_tenant(tenant_id: str) -> list[dict[str, Any]]:
    return db.query("orders", tenant_id=tenant_id, deleted=False)


def soft_delete(order_id: str) -> None:
    row = db.get("orders", order_id)
    if not row:
        raise KeyError(order_id)
    row["deleted"] = True
    row["status"] = "cancelled"
    db.put("orders", order_id, row)


def update_status(order_id: str, status: str) -> dict[str, Any]:
    row = db.get("orders", order_id)
    if not row:
        raise KeyError(order_id)
    row["status"] = status
    db.put("orders", order_id, row)
    return row


def create(order_id: str, tenant_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    row = {
        "id": order_id,
        "tenant_id": tenant_id,
        "items": items,
        "status": "created",
        "deleted": False,
    }
    db.put("orders", order_id, row)
    return row
