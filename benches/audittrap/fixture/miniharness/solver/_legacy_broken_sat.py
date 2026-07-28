"""Intentionally alternate SAT implementation (unused by pipeline)."""

from __future__ import annotations


def broken_sat_solve(n: int, clauses: list[list[int]]) -> dict[int, bool] | None:
    assign: dict[int, bool] = {}

    def dpll(cls: list[list[int]]) -> bool:
        if not cls:
            return True
        v = abs(cls[0][0])
        for bit in (True, False):
            assign[v] = bit
            if dpll(cls):
                return True
            assign.clear()
        return False

    return {i: False for i in range(1, n + 1)} if dpll([c[:] for c in clauses]) else None
