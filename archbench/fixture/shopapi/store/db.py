from __future__ import annotations

from typing import Any

# In-memory stand-in. Keys are table names.
_TABLES: dict[str, dict[str, dict[str, Any]]] = {
    "orders": {},
    "invoices": {},
    "payments": {},
    "outbox": {},
    "processed_webhooks": {},
}


def reset() -> None:
    for t in _TABLES:
        _TABLES[t].clear()


def get(table: str, key: str) -> dict[str, Any] | None:
    return _TABLES[table].get(key)


def put(table: str, key: str, row: dict[str, Any]) -> None:
    _TABLES[table][key] = row


def delete(table: str, key: str) -> None:
    _TABLES[table].pop(key, None)


def scan(table: str) -> list[dict[str, Any]]:
    return list(_TABLES[table].values())


def query(table: str, **eq: Any) -> list[dict[str, Any]]:
    out = []
    for row in _TABLES[table].values():
        if all(row.get(k) == v for k, v in eq.items()):
            out.append(row)
    return out
