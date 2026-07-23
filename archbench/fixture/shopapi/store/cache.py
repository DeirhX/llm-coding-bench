from __future__ import annotations

from typing import Any

_CACHE: dict[str, Any] = {}


def cache_key_order(order_id: str) -> str:
    return f"order:{order_id}"


def get(key: str) -> Any | None:
    return _CACHE.get(key)


def set(key: str, value: Any) -> None:
    _CACHE[key] = value


def invalidate(key: str) -> None:
    _CACHE.pop(key, None)


def invalidate_order(order_id: str) -> None:
    invalidate(cache_key_order(order_id))


def clear() -> None:
    _CACHE.clear()
