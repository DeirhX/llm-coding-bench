from __future__ import annotations

from typing import Any
import uuid

from store import ledger_repo, outbox
from pkg import events
from pkg.money import Money
from pkg.tenant import require_tenant


def post_entry(amount: Money, memo: str = "") -> dict[str, Any]:
    ctx = require_tenant()
    entry = {
        "id": str(uuid.uuid4()),
        "tenant_id": ctx.tenant_id,
        "cents": amount.cents,
        "currency": amount.currency,
        "memo": memo,
        "source": "live",
    }
    ledger_repo.append(entry)
    outbox.insert(events.ledger_entry_posted(ctx.tenant_id, entry["id"]))
    return entry


def split_credit(total: Money, parts: int) -> list[Money]:
    return total.split(parts)


def list_entries() -> list[dict[str, Any]]:
    ctx = require_tenant()
    return ledger_repo.list_by_tenant(ctx.tenant_id)
