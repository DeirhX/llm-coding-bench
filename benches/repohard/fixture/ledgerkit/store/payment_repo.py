from __future__ import annotations
from typing import Any
from store import db

_STATS = {"list_by_invoice": 0}


def reset_stats() -> None:
    _STATS["list_by_invoice"] = 0


def stats() -> dict[str, int]:
    return dict(_STATS)


def upsert(p: dict[str, Any]) -> dict[str, Any]:
    db.put("payments", p["id"], p)
    return p

def get(payment_id: str) -> dict[str, Any] | None:
    return db.get("payments", payment_id)

def list_by_invoice(invoice_id: str) -> list[dict[str, Any]]:
    _STATS["list_by_invoice"] += 1
    return db.list_where("payments", invoice_id=invoice_id)


def list_by_tenant(tenant_id: str) -> list[dict[str, Any]]:
    return db.list_where("payments", tenant_id=tenant_id)
