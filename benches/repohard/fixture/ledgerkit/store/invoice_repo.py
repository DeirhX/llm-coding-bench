from __future__ import annotations
from typing import Any
from store import db

def upsert(inv: dict[str, Any]) -> dict[str, Any]:
    db.put("invoices", inv["id"], inv)
    return inv

def get(invoice_id: str) -> dict[str, Any] | None:
    return db.get("invoices", invoice_id)

def list_by_tenant(tenant_id: str) -> list[dict[str, Any]]:
    return db.list_where("invoices", tenant_id=tenant_id)

def list_unpaid(tenant_id: str) -> list[dict[str, Any]]:
    return [r for r in list_by_tenant(tenant_id) if r.get("status") != "paid"]

def export_all() -> list[dict[str, Any]]:
    return db.list_all("invoices")
