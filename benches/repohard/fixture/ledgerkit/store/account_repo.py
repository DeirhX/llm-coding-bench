from __future__ import annotations
from typing import Any
from store import db

def _key(tenant_id: str, account_id: str) -> str:
    return f"{tenant_id}:{account_id}"


def upsert(account: dict[str, Any]) -> dict[str, Any]:
    db.put("accounts", _key(account["tenant_id"], account["id"]), account)
    return account

def get(account_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
    if tenant_id is not None:
        return db.get("accounts", _key(tenant_id, account_id))
    # legacy bare-id lookup (ambiguous)
    for row in db.list_all("accounts"):
        if row.get("id") == account_id:
            return row
    return None

def list_by_tenant(tenant_id: str) -> list[dict[str, Any]]:
    return db.list_where("accounts", tenant_id=tenant_id)
