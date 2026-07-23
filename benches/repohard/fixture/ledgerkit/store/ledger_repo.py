from __future__ import annotations
from typing import Any
from store import db

def append(entry: dict[str, Any]) -> dict[str, Any]:
    db.put("ledger", entry["id"], entry)
    return entry

def get(entry_id: str) -> dict[str, Any] | None:
    return db.get("ledger", entry_id)

def list_by_tenant(tenant_id: str) -> list[dict[str, Any]]:
    return db.list_where("ledger", tenant_id=tenant_id)

def list_legacy_unmigrated() -> list[dict[str, Any]]:
    return [r for r in db.list_all("ledger_legacy") if not r.get("migrated")]
