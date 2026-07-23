#!/usr/bin/env python3.14
"""Per-case autopsy + partial scores for pyhard failures."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

OUT = Path.home() / ".ollama" / "bench" / "results"
PY = sys.executable

CASES = {
    "unify": {
        "max": 10,
        "setup": textwrap.dedent(
            """
            def deep_subst(t, env):
                if isinstance(t, tuple) and t and t[0] == "var":
                    return deep_subst(env[t[1]], env) if t[1] in env else t
                if isinstance(t, tuple) and t and t[0] == "fn":
                    return ("fn", t[1], [deep_subst(a, env) for a in t[2]])
                return t
            """
        ),
        "cases": [
            ("const_eq", "got = unify(1, 1); assert got is not None"),
            ("var_const", "got = unify(('var','X'), 3); assert got is not None and deep_subst(('var','X'), got) == 3"),
            ("alias", "got = unify(('var','X'), ('var','Y')); assert got is not None and deep_subst(('var','X'), got) == deep_subst(('var','Y'), got)"),
            ("fn_bind", "got = unify(('fn','f',[('var','X'),1]), ('fn','f',[2,1])); assert got is not None and deep_subst(('var','X'), got) == 2"),
            ("fn_nested", "got = unify(('fn','f',[('var','X')]), ('fn','f',[('fn','g',[1])])); assert got is not None and deep_subst(('var','X'), got) == ('fn','g',[1])"),
            ("const_neq", "assert unify(1, 2) is None"),
            ("fn_name", "assert unify(('fn','f',[1]), ('fn','g',[1])) is None"),
            ("fn_arity", "assert unify(('fn','f',[1]), ('fn','f',[1,2])) is None"),
            ("occurs", "assert unify(('var','X'), ('fn','f',[('var','X')])) is None"),
            ("conflict", "assert unify(('fn','f',[('var','X'),('var','X')]), ('fn','f',[1,2])) is None"),
        ],
    },
    "mini_sql": {
        "max": 8,
        "setup": textwrap.dedent(
            """
            tables = {
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
            """
        ),
        "cases": [
            ("where_eq", "assert execute_select(tables, \"SELECT name FROM users WHERE age = 30\") == [{'name': 'Ann'}, {'name': 'Cy'}]"),
            ("where_gt", "assert execute_select(tables, \"SELECT name FROM users WHERE age > 25\") == [{'name': 'Ann'}, {'name': 'Cy'}]"),
            ("where_ne", "assert execute_select(tables, \"SELECT id FROM users WHERE name != 'Bob'\") == [{'id': 1}, {'id': 3}]"),
            ("join_where", "assert execute_select(tables, \"SELECT users.name, orders.total FROM users JOIN orders ON users.id = orders.user_id WHERE orders.total >= 40\") == [{'users.name': 'Ann', 'orders.total': 50}, {'users.name': 'Bob', 'orders.total': 40}, {'users.name': 'Cy', 'orders.total': 40}]"),
            ("join_and", "assert execute_select(tables, \"SELECT orders.id FROM users JOIN orders ON users.id = orders.user_id WHERE users.name = 'Ann' AND orders.total < 10\") == [{'orders.id': 11}]"),
            ("select_all", "assert execute_select(tables, \"SELECT id FROM users\") == [{'id': 1}, {'id': 2}, {'id': 3}]"),
            ("join_filter", "assert execute_select(tables, \"SELECT users.id, orders.id FROM users JOIN orders ON users.id = orders.user_id WHERE users.age = 20\") == [{'users.id': 2, 'orders.id': 12}]"),
            ("where_and", "assert execute_select(tables, \"SELECT name FROM users WHERE age >= 30 AND name != 'Cy'\") == [{'name': 'Ann'}]"),
        ],
    },
    "sat_solve": {
        "max": 10,
        "setup": textwrap.dedent(
            """
            def ok_assign(n, clauses, asg):
                if asg is None:
                    return False
                if set(asg) != set(range(1, n + 1)):
                    return False
                for c in clauses:
                    if not any((asg[abs(l)] if l > 0 else (not asg[abs(l)])) for l in c):
                        return False
                return True
            """
        ),
        "cases": [
            ("unit_pos", "got=sat_solve(1,[[1]]); assert ok_assign(1,[[1]],got)"),
            ("unit_neg", "got=sat_solve(1,[[-1]]); assert ok_assign(1,[[-1]],got)"),
            ("conflict", "assert sat_solve(1,[[1],[-1]]) is None"),
            ("sat2", "cls=[[1,2],[-1,2],[1,-2]]; got=sat_solve(2,[c[:] for c in cls]); assert ok_assign(2,cls,got)"),
            ("sat3", "cls=[[1,2],[-1,3],[-2,-3],[1,-2,3]]; got=sat_solve(3,[c[:] for c in cls]); assert ok_assign(3,cls,got)"),
            ("unsat_extra", "assert sat_solve(2,[[1],[-1],[2]]) is None"),
            ("unsat3", "assert sat_solve(3,[[1,2,3],[-1],[-2],[-3]]) is None"),
            ("sat4", "cls=[[1,2],[3,4],[-1,-3],[-2,-4],[1,4]]; got=sat_solve(4,[c[:] for c in cls]); assert ok_assign(4,cls,got)"),
            ("tautology", "cls=[[1,-1],[2]]; got=sat_solve(2,[c[:] for c in cls]); assert ok_assign(2,cls,got)"),
            ("unsat_xor", "assert sat_solve(2,[[1,2],[1,-2],[-1,2],[-1,-2]]) is None"),
        ],
    },
}


def run_case(code: str, setup: str, case_src: str) -> tuple[bool, str]:
    src = code + "\n" + setup + "\n" + case_src + "\nprint('OK')\n"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.py"
        p.write_text(src, encoding="utf-8")
        try:
            proc = subprocess.run([PY, str(p)], capture_output=True, text=True, timeout=3)
        except subprocess.TimeoutExpired:
            return False, "TIMEOUT"
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0 and "OK" in proc.stdout:
            return True, "OK"
        err = out.strip().splitlines()
        return False, (err[-1] if err else f"exit {proc.returncode}")[:160]


def analyze(prefix: str, model_name: str, tasks: list[str]) -> dict:
    report = {"model": model_name, "tasks": {}}
    for task in tasks:
        code_path = OUT / f"{prefix}__{task}__code.py"
        if not code_path.exists():
            continue
        code = code_path.read_text(encoding="utf-8")
        spec = CASES[task]
        results = []
        for name, src in spec["cases"]:
            ok, detail = run_case(code, spec["setup"], src)
            results.append({"case": name, "ok": ok, "detail": detail})
        score = sum(1 for r in results if r["ok"])
        report["tasks"][task] = {
            "score": score,
            "max": spec["max"],
            "cases": results,
        }
    return report


def main() -> None:
    reports = [
        analyze(
            "qwen3-coder-next_q8_0_pyhard",
            "qwen3-coder-next:q8_0",
            ["unify", "mini_sql"],
        ),
        analyze(
            "qwen3-coder_30b-a3b-fp16_pyhard",
            "qwen3-coder:30b-a3b-fp16",
            ["sat_solve", "mini_sql"],
        ),
    ]

    # Full official scores with partial fail credit
    official = {}
    for p in OUT.glob("*_pyhard_pyhard_latest.json"):
        rows = json.loads(p.read_text())
        model = rows[0]["model"]
        official[model] = {r["task"]: r for r in rows}

    lines = ["# Pyhard failure autopsy (per-case rescoring)", ""]
    for rep in reports:
        model = rep["model"]
        lines.append(f"## {model}")
        lines.append("")
        base = official.get(model, {})
        total = 0
        tmax = 0
        for task, row in (base or {}).items():
            if task in rep["tasks"]:
                s = rep["tasks"][task]["score"]
                m = rep["tasks"][task]["max"]
            else:
                s, m = row["score"], row["max_score"]
            total += s
            tmax += m
            flag = "PASS" if s == m else "FAIL"
            lines.append(f"- {task}: {flag} **{s}/{m}**" + (" (rescored)" if task in rep["tasks"] else ""))
        lines.append(f"- **rescored total: {total}/{tmax}**")
        lines.append("")
        for task, info in rep["tasks"].items():
            lines.append(f"### {task} → {info['score']}/{info['max']}")
            for c in info["cases"]:
                mark = "✓" if c["ok"] else "✗"
                extra = "" if c["ok"] else f" — {c['detail']}"
                lines.append(f"- {mark} `{c['case']}`{extra}")
            lines.append("")

    # Root-cause notes
    lines += [
        "## Root causes",
        "",
        "### Next — `unify`",
        "- Constant–constant success returns `True` (bool) instead of the `env` dict.",
        "- Fn-arg loop then does `current_env.update(result)` → `TypeError: 'bool' object is not iterable`.",
        "- So it dies on the first easy case (`unify(1,1)`), not on occurs-check.",
        "- Design was close; the return-type bug is a one-liner class of faceplant.",
        "",
        "### Next — `mini_sql`",
        "- `FROM` regex is `(?:\\s+(JOIN|WHERE|$))`, which **consumes the WHERE keyword**.",
        "- `where_clause` is then searched in the remainder and never found → WHERE ignored.",
        "- Confirmed: `SELECT name FROM users WHERE age = 30` returns Ann/Bob/Cy.",
        "- Cases without WHERE (e.g. `SELECT id FROM users`) can still pass.",
        "",
        "### 30B — `sat_solve`",
        "- Dedup key is `tuple(sorted(abs(lit) for lit in clause))` — **throws away polarity**.",
        "- `[[1],[-1]]` collapses to a single clause → reports sat with `{1: True}`.",
        "- Classic “I invented a clever preprocess and murdered completeness” bug.",
        "",
        "### 30B — `mini_sql`",
        "- `JOIN ... ON (.+)` is greedy and swallows the trailing `WHERE ...`.",
        "- `ON` string becomes `users.id = orders.user_id WHERE ...`, then `split('=')` ≠ 2 parts → hard error.",
        "- Non-JOIN queries can still pass; JOIN cases all die.",
        "",
    ]

    path = OUT / "pyhard_failure_autopsy.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"Wrote {path}")


if __name__ == "__main__":
    main()
