"""Tiny DPLL SAT solver."""

from __future__ import annotations


def sat_solve(n: int, clauses: list[list[int]]) -> dict[int, bool] | None:
    assign: dict[int, bool] = {}

    def value(lit: int) -> bool | None:
        v = abs(lit)
        if v not in assign:
            return None
        return assign[v] if lit > 0 else (not assign[v])

    def simplify(cls: list[list[int]]) -> list[list[int]] | None:
        out: list[list[int]] = []
        for c in cls:
            sat = False
            nc: list[int] = []
            for lit in c:
                val = value(lit)
                if val is True:
                    sat = True
                    break
                if val is None:
                    nc.append(lit)
            if sat:
                continue
            if not nc:
                return None
            out.append(nc)
        return out

    def dpll(cls: list[list[int]]) -> bool:
        while True:
            s = simplify(cls)
            if s is None:
                return False
            cls = s
            unit = next((c[0] for c in cls if len(c) == 1), None)
            if unit is None:
                break
            assign[abs(unit)] = unit > 0
        if not cls:
            for v in range(1, n + 1):
                assign.setdefault(v, False)
            return True
        lit = cls[0][0]
        v = abs(lit)
        for bit in (True, False):
            assign[v] = bit
            snap = dict(assign)
            if dpll([c[:] for c in cls]):
                return True
            assign.clear()
            assign.update(snap)
            del assign[v]
        return False

    ok = dpll([c[:] for c in clauses])
    if not ok:
        return None
    return {v: bool(assign.get(v, False)) for v in range(1, n + 1)}
