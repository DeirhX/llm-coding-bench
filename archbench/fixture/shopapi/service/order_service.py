from __future__ import annotations

from typing import Any

from pkg.events import order_cancelled, order_status_changed
from pkg.tenant import require_tenant
from store import cache, order_repo, outbox


def get_order(order_id: str) -> dict[str, Any]:
    tenant = require_tenant()
    cached = cache.get(cache.cache_key_order(order_id))
    if cached is not None:
        return cached
    row = order_repo.get(order_id)
    if not row or row["tenant_id"] != tenant.tenant_id:
        raise KeyError(order_id)
    cache.set(cache.cache_key_order(order_id), row)
    return row


def list_orders() -> list[dict[str, Any]]:
    """List orders for current tenant.

    PLANTED SMELL: N+1 — loads line item enrichment per order via get_order
    (and thus cache/db) instead of a single query.
    """
    tenant = require_tenant()
    rows = order_repo.list_by_tenant(tenant.tenant_id)
    enriched = []
    for row in rows:
        # N+1: re-fetch each order individually
        enriched.append(get_order(row["id"]))
    return enriched


def cancel_order(order_id: str) -> dict[str, Any]:
    """Cancel order: soft-delete, outbox OrderCancelled, invalidate cache."""
    tenant = require_tenant()
    row = order_repo.get(order_id)
    if not row or row["tenant_id"] != tenant.tenant_id:
        raise KeyError(order_id)
    order_repo.soft_delete(order_id)
    outbox.insert(order_cancelled(order_id, tenant.tenant_id))
    cache.invalidate_order(order_id)
    return {"id": order_id, "status": "cancelled"}


def mark_paid(order_id: str, payment_id: str) -> dict[str, Any]:
    tenant = require_tenant()
    row = order_repo.get(order_id)
    if not row or row["tenant_id"] != tenant.tenant_id:
        raise KeyError(order_id)
    updated = order_repo.update_status(order_id, "paid")
    # PLANTED BUG (I4): status changed to paid but cache NOT invalidated.
    outbox.insert(order_status_changed(order_id, tenant.tenant_id, "paid"))
    # payment event inserted by payment_service, not here
    return updated
