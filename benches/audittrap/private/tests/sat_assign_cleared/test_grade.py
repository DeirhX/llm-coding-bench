"""Private grade for wontfix_sat_backtrack — correct code must stay."""

from __future__ import annotations

from pathlib import Path

from miniharness.solver.sat import sat_solve


def test_snap_restore_pattern_intact() -> None:
    src = Path("solver/sat.py").read_text(encoding="utf-8")
    assert "snap = dict(assign)" in src
    assert "assign.clear()" in src
    assert "assign.update(snap)" in src
    assert "del assign[v]" in src


def test_needs_backtracking() -> None:
    # (x ∨ y) ∧ (¬x ∨ y) ∧ (x ∨ ¬y) ∧ (¬x ∨ ¬y) is unsat — forces both branches.
    clauses = [[1, 2], [-1, 2], [1, -2], [-1, -2]]
    assert sat_solve(2, clauses) is None


def test_sat_instance() -> None:
    # Needs trying False then True on v=1.
    clauses = [[1, 2], [-1, 2], [2]]
    m = sat_solve(2, clauses)
    assert m is not None
    assert m[2] is True
