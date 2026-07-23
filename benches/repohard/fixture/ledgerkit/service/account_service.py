from __future__ import annotations

from typing import Any

from store import account_repo, cache
from pkg.tenant import require_tenant


def get_account(account_id: str) -> dict[str, Any] | None:
    ctx = require_tenant()
    key = cache.cache_key_account(account_id)
    hit = cache.get(key)
    if hit is not None:
        return hit
    row = account_repo.get(account_id, tenant_id=ctx.tenant_id)
    if row is None:
        return None
    cache.set(key, row)
    return row


def create_account(account_id: str, name: str) -> dict[str, Any]:
    ctx = require_tenant()
    row = {"id": account_id, "tenant_id": ctx.tenant_id, "name": name}
    return account_repo.upsert(row)


def list_accounts() -> list[dict[str, Any]]:
    ctx = require_tenant()
    return account_repo.list_by_tenant(ctx.tenant_id)
