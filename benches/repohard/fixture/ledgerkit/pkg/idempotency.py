from __future__ import annotations

from store import db


def seen(scope: str, key: str) -> bool:
    return db.get(f"idemp:{scope}", key) is not None


def remember(scope: str, key: str) -> None:
    db.put(f"idemp:{scope}", key, {"ok": True})
