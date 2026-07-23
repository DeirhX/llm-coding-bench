from __future__ import annotations

from typing import Any
import time
import uuid


def _base(kind: str, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "tenant_id": tenant_id,
        "payload": payload,
        "ts": time.time(),
    }


def invoice_paid(tenant_id: str, invoice_id: str, payment_id: str) -> dict[str, Any]:
    return _base("InvoicePaid", tenant_id, {"invoice_id": invoice_id, "payment_id": payment_id})


def entitlement_changed(tenant_id: str, account_id: str, plan: str) -> dict[str, Any]:
    return _base("EntitlementChanged", tenant_id, {"account_id": account_id, "plan": plan})


def ledger_entry_posted(tenant_id: str, entry_id: str) -> dict[str, Any]:
    return _base("LedgerEntryPosted", tenant_id, {"entry_id": entry_id})
