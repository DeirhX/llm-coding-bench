"""Toy relational tables for the mini-SQL selftest."""

from __future__ import annotations

TABLES: dict[str, list[dict]] = {
    "users": [
        {"id": 1, "name": "Ann", "age": 30},
        {"id": 2, "name": "Bob", "age": 20},
        {"id": 3, "name": "Cy", "age": 30},
    ],
    "orders": [
        {"id": 10, "user_id": 1, "total": 50},
        {"id": 11, "user_id": 1, "total": 5},
        {"id": 12, "user_id": 2, "total": 40},
        {"id": 13, "user_id": 3, "total": 40},
    ],
}
