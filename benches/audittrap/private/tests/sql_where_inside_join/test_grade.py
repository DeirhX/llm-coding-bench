"""Private grade for wontfix_sql_where_nest — correct regex must stay."""

from __future__ import annotations

from pathlib import Path

from miniharness.schema import TABLES
from miniharness.solver.sql import execute_select


def test_where_sibling_not_nested() -> None:
    src = Path("solver/sql.py").read_text(encoding="utf-8")
    # Sibling optional groups: JOIN closes before WHERE opens.
    assert r"(?: JOIN" in src or "(?: JOIN" in src
    assert r"(?: WHERE" in src or "(?: WHERE" in src
    # Must not nest WHERE inside the JOIN group as a single optional.
    assert "JOIN" in src and "WHERE" in src


def test_where_without_join() -> None:
    rows = execute_select(TABLES, "SELECT name FROM users WHERE age = 30")
    assert {r["name"] for r in rows} == {"Ann", "Cy"}


def test_join_with_where() -> None:
    rows = execute_select(
        TABLES,
        "SELECT users.name FROM users JOIN orders ON users.id = orders.user_id WHERE total = 50",
    )
    assert rows == [{"users.name": "Ann"}]


def test_bare_total_unambiguous() -> None:
    rows = execute_select(
        TABLES,
        "SELECT total FROM users JOIN orders ON users.id = orders.user_id WHERE name = 'Bob'",
    )
    assert rows == [{"total": 40}]
