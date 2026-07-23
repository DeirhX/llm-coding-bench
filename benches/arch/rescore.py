#!/usr/bin/env python3.14
"""Re-grade saved archbench result JSON with the current graders.

Rebuilds ToolSession.files_read from each row's recorded reads (no model calls).
Writes ``*_arch_rescored.json`` next to each input and prints a before/after table.

Usage:
  python3.14 -m benches.arch.rescore
  python3.14 -m benches.arch.rescore results/archbench/cursor_claude-sonnet-5-high_arch_latest.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benches.arch.tasks import _GRADERS  # noqa: E402
from benches.shopapi.tools import ToolSession  # noqa: E402
from bench_lib.paths import results_dir  # noqa: E402


def rescore_rows(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict) or "task" not in row:
            out.append(row)
            continue
        task = row["task"]
        grade_fn = _GRADERS.get(task)
        if grade_fn is None or "answer" not in row:
            out.append(row)
            continue
        session = ToolSession()
        for f in row.get("files_read") or []:
            session.files_read.add(str(f))
        g = grade_fn(row.get("answer") or {}, session)
        new = dict(row)
        new["score"] = int(g["score"])
        new["max_score"] = int(g["max_score"])
        new["ok"] = bool(g.get("ok"))
        new["grade_detail"] = g.get("detail")
        new["rescored"] = True
        new["score_before"] = row.get("score")
        out.append(new)
    return out


def totals(rows: list[dict]) -> tuple[int, int]:
    return (
        sum(int(r.get("score") or 0) for r in rows if isinstance(r, dict)),
        sum(int(r.get("max_score") or 0) for r in rows if isinstance(r, dict)),
    )


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        paths = [Path(a) for a in argv[1:]]
    else:
        paths = sorted(results_dir("archbench").glob("*_arch_latest.json"))

    print(f"{'file':<56} {'before':>8} {'after':>8}")
    for path in paths:
        if not path.is_file():
            print(f"missing {path}", file=sys.stderr)
            continue
        obj = json.loads(path.read_text())
        if not isinstance(obj, list):
            print(f"skip {path.name} (not a task list)")
            continue
        before_s, before_m = totals(obj)
        rescored = rescore_rows(obj)
        after_s, after_m = totals(rescored)
        if path.name.endswith("_arch_latest.json"):
            out = path.with_name(path.name.replace("_arch_latest.json", "_arch_rescored_latest.json"))
        else:
            out = path.with_name(path.stem + "_rescored.json")
        out.write_text(json.dumps(rescored, indent=2) + "\n")
        delta = after_s - before_s
        flag = f"  ({delta:+d})" if delta else ""
        print(f"{path.name:<56} {before_s:>3}/{before_m:<3}  {after_s:>3}/{after_m:<3}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
