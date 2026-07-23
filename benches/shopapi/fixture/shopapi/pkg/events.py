from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Event:
    name: str
    payload: dict[str, Any]
    tenant_id: str


def order_cancelled(order_id: str, tenant_id: str) -> Event:
    return Event("OrderCancelled", {"order_id": order_id}, tenant_id)


def order_paid(order_id: str, tenant_id: str, payment_id: str) -> Event:
    return Event(
        "OrderPaid",
        {"order_id": order_id, "payment_id": payment_id},
        tenant_id,
    )


def order_status_changed(order_id: str, tenant_id: str, status: str) -> Event:
    return Event(
        "OrderStatusChanged",
        {"order_id": order_id, "status": status},
        tenant_id,
    )
