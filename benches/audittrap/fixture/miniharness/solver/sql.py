"""Mini SQL SELECT/JOIN/WHERE."""

from __future__ import annotations

import re
from typing import Any


_SELECT_RE = re.compile(
    r"SELECT (.+?) FROM (\w+)"
    r"(?: JOIN (\w+) ON (\w+)\.(\w+) = (\w+)\.(\w+))?"
    r"(?: WHERE (.+))?$",
    flags=re.I,
)


def execute_select(tables: dict[str, list[dict]], query: str) -> list[dict[str, Any]]:
    q = " ".join(query.strip().split())
    m = _SELECT_RE.match(q)
    if not m:
        raise ValueError("unsupported SQL")
    cols_s, t1, t2, a_t, a_c, b_t, b_c, where = m.groups()
    cols = [c.strip() for c in cols_s.split(",")]

    def resolve(rowmap: dict[str, dict], name: str) -> Any:
        if "." in name:
            t, c = name.split(".", 1)
            return rowmap[t][c]
        hits = []
        for _t, row in rowmap.items():
            if name in row:
                hits.append(row[name])
        if len(hits) != 1:
            raise KeyError(name)
        return hits[0]

    rows: list[dict[str, dict]] = []
    if t2:
        for r1 in tables[t1]:
            for r2 in tables[t2]:
                rm = {t1: r1, t2: r2}
                if rm[a_t][a_c] == rm[b_t][b_c]:
                    rows.append(rm)
    else:
        for r1 in tables[t1]:
            rows.append({t1: r1})

    def eval_where(rm: dict[str, dict], expr: str) -> bool:
        parts = re.split(r"\s+AND\s+", expr, flags=re.I)
        for part in parts:
            part = part.strip()
            m2 = re.match(
                r"(.+?)\s*(>=|<=|!=|=|>|<)\s*(.+)$",
                part,
            )
            if not m2:
                raise ValueError(f"bad where atom: {part}")
            left_s, op, right_s = m2.groups()
            left = resolve(rm, left_s.strip())
            right_s = right_s.strip()
            if (right_s.startswith("'") and right_s.endswith("'")) or (
                right_s.startswith('"') and right_s.endswith('"')
            ):
                right: Any = right_s[1:-1]
            else:
                try:
                    right = int(right_s)
                except ValueError:
                    right = resolve(rm, right_s)
            if op == "=" and not (left == right):
                return False
            if op == "!=" and not (left != right):
                return False
            if op == ">" and not (left > right):
                return False
            if op == "<" and not (left < right):
                return False
            if op == ">=" and not (left >= right):
                return False
            if op == "<=" and not (left <= right):
                return False
        return True

    if where:
        rows = [rm for rm in rows if eval_where(rm, where)]

    out: list[dict[str, Any]] = []
    for rm in rows:
        item: dict[str, Any] = {}
        for col in cols:
            if "." in col:
                item[col] = resolve(rm, col)
            else:
                item[col] = resolve(rm, col)
        out.append(item)
    return out
