from __future__ import annotations

from typing import Any

_TABLES: dict[str, dict[str, Any]] = {}


def reset() -> None:
    _TABLES.clear()


def table(name: str) -> dict[str, Any]:
    return _TABLES.setdefault(name, {})


def put(name: str, key: str, value: Any) -> None:
    table(name)[key] = value


def get(name: str, key: str) -> Any | None:
    return table(name).get(key)


def delete(name: str, key: str) -> None:
    table(name).pop(key, None)


def list_all(name: str) -> list[Any]:
    return list(table(name).values())


def list_where(name: str, **preds: Any) -> list[Any]:
    rows = []
    for row in list_all(name):
        if all(row.get(k) == v for k, v in preds.items()):
            rows.append(row)
    return rows
