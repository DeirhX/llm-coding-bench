from __future__ import annotations

from typing import Any
import uuid
import threading

from store import payment_repo, webhook_repo, outbox, invoice_repo
from service import invoice_service, entitlement_service
from pkg import events
from pkg.tenant import require_tenant, set_tenant, TenantContext


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        if key not in _LOCKS:
            _LOCKS[key] = threading.Lock()
        return _LOCKS[key]


def handle_payment_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """Process payment.succeeded webhook."""
    webhook_id = payload["webhook_id"]
    tenant_id = payload["tenant_id"]
    invoice_id = payload["invoice_id"]
    amount_cents = int(payload["amount_cents"])

    set_tenant(TenantContext(tenant_id=tenant_id))
    pay = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "invoice_id": invoice_id,
        "amount_cents": amount_cents,
        "webhook_id": webhook_id,
    }
    payment_repo.upsert(pay)
    invoice_service.mark_paid(invoice_id)
    inv = invoice_repo.get(invoice_id) or {}
    account_id = inv.get("account_id") or payload.get("account_id")
    if account_id:
        entitlement_service.activate_paid_plan(account_id, tenant_id)
    outbox.insert(events.invoice_paid(tenant_id, invoice_id, pay["id"]))
    webhook_repo.mark_processed(webhook_id, {"payment_id": pay["id"]})
    return {"status": "ok", "payment_id": pay["id"], "webhook_id": webhook_id}


def record_payment(invoice_id: str, amount_cents: int) -> dict[str, Any]:
    ctx = require_tenant()
    pay = {
        "id": str(uuid.uuid4()),
        "tenant_id": ctx.tenant_id,
        "invoice_id": invoice_id,
        "amount_cents": amount_cents,
    }
    return payment_repo.upsert(pay)
