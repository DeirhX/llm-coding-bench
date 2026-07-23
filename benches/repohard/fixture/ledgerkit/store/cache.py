from __future__ import annotations

from typing import Any

_CACHE: dict[str, Any] = {}


def reset() -> None:
    _CACHE.clear()


def cache_key_account(account_id: str) -> str:
    return f"acct:{account_id}"


def cache_key_invoice(invoice_id: str) -> str:
    return f"inv:{invoice_id}"


def get(key: str) -> Any | None:
    return _CACHE.get(key)


def set(key: str, value: Any) -> None:
    _CACHE[key] = value


def invalidate(key: str) -> None:
    _CACHE.pop(key, None)
