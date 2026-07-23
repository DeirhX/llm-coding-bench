from __future__ import annotations

from typing import Any

from pkg.events import order_paid
from pkg.tenant import require_tenant
from store import db, outbox
from service import order_service


def _already_processed(webhook_id: str) -> bool:
    return db.get("processed_webhooks", webhook_id) is not None


def _mark_processed(webhook_id: str) -> None:
    db.put("processed_webhooks", webhook_id, {"id": webhook_id})


def handle_payment_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """Process provider webhook.

    PLANTED BUG (I3 / duplicate OrderPaid):
    We write the outbox OrderPaid event BEFORE recording webhook idempotency.
    If the process crashes after outbox.insert but before _mark_processed,
    a retry inserts a second OrderPaid for the same payment.
    """
    tenant = require_tenant()
    webhook_id = payload["webhook_id"]
    order_id = payload["order_id"]
    payment_id = payload["payment_id"]

    if _already_processed(webhook_id):
        return {"status": "duplicate", "order_id": order_id}

    # Charge bookkeeping
    db.put(
        "payments",
        payment_id,
        {
            "id": payment_id,
            "order_id": order_id,
            "tenant_id": tenant.tenant_id,
            "amount": payload.get("amount", 0),
        },
    )

    # Side effects before idempotency commit — race / crash window
    outbox.insert(order_paid(order_id, tenant.tenant_id, payment_id))
    order_service.mark_paid(order_id, payment_id)

    _mark_processed(webhook_id)
    return {"status": "ok", "order_id": order_id, "payment_id": payment_id}
