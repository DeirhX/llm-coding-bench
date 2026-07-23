from __future__ import annotations
from typing import Any
from store import db

def set_plan(account_id: str, tenant_id: str, plan: str) -> dict[str, Any]:
    row = {"account_id": account_id, "tenant_id": tenant_id, "plan": plan}
    db.put("entitlements", account_id, row)
    return row

def get(account_id: str) -> dict[str, Any] | None:
    return db.get("entitlements", account_id)
