from __future__ import annotations

from typing import Any
import uuid

from store import invoice_repo, cache
from pkg.tenant import require_tenant
from pkg.money import Money


def create_invoice(account_id: str, amount: Money, lines: list[dict] | None = None) -> dict[str, Any]:
    ctx = require_tenant()
    inv = {
        "id": str(uuid.uuid4()),
        "tenant_id": ctx.tenant_id,
        "account_id": account_id,
        "amount_cents": amount.cents,
        "currency": amount.currency,
        "status": "open",
        "lines": lines or [],
    }
    invoice_repo.upsert(inv)
    return inv


def get_invoice(invoice_id: str) -> dict[str, Any] | None:
    key = cache.cache_key_invoice(invoice_id)
    hit = cache.get(key)
    if hit is not None:
        return hit
    row = invoice_repo.get(invoice_id)
    if row:
        cache.set(key, row)
    return row


def mark_paid(invoice_id: str) -> dict[str, Any]:
    inv = invoice_repo.get(invoice_id)
    if inv is None:
        raise KeyError(invoice_id)
    inv = {**inv, "status": "paid"}
    invoice_repo.upsert(inv)
    cache.invalidate(cache.cache_key_invoice(invoice_id))
    return inv


def serialize_public(inv: dict[str, Any]) -> dict[str, Any]:
    """Public JSON shape for API responses."""
    return {
        "id": inv["id"],
        "tenant_id": inv["tenant_id"],
        "account_id": inv["account_id"],
        "amount_cents": inv["amount_cents"],
        "currency": inv.get("currency", "USD"),
        "status": inv["status"],
    }
