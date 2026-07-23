from __future__ import annotations

from typing import Any

from . import db


def list_by_tenant(tenant_id: str) -> list[dict[str, Any]]:
    return db.query("invoices", tenant_id=tenant_id)


def get_by_id(invoice_id: str) -> dict[str, Any] | None:
    """BUG (planted): no tenant filter — caller must enforce."""
    return db.get("invoices", invoice_id)


def create(invoice_id: str, tenant_id: str, order_id: str, amount: int) -> dict[str, Any]:
    row = {
        "id": invoice_id,
        "tenant_id": tenant_id,
        "order_id": order_id,
        "amount": amount,
    }
    db.put("invoices", invoice_id, row)
    return row


def export_all() -> list[dict[str, Any]]:
    """Admin export — intentionally cross-tenant."""
    return db.scan("invoices")
