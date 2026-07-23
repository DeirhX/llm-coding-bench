from __future__ import annotations

from typing import Any

from store import entitlement_repo, outbox
from pkg import events


def activate_paid_plan(account_id: str, tenant_id: str) -> dict[str, Any]:
    row = entitlement_repo.set_plan(account_id, tenant_id, "pro")
    outbox.insert(events.entitlement_changed(tenant_id, account_id, "pro"))
    return row


def get_plan(account_id: str) -> str | None:
    row = entitlement_repo.get(account_id)
    return None if row is None else row.get("plan")
