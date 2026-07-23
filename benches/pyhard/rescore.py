#!/usr/bin/env python3.14
"""Re-grade saved pyhard __code.py / .txt with the current per-case harness.

When artifacts are missing, applies published autopsy overrides for known runs.

Usage:
  python3.14 -m benches.pyhard.rescore
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ.setdefault("BENCH_SELFTEST", "1")

from benches.pyhard import bench as B  # noqa: E402
from bench_lib.paths import results_dir  # noqa: E402

OUT = results_dir()

GRADERS = {
    "regex_match": B.grade_regex_match,
    "lru_cache": B.grade_lru_cache,
    "alien_order": B.grade_alien_order,
    "eval_expr": B.grade_eval_expr,
    "fix_vm": B.grade_fix_vm,
    "sat_solve": B.grade_sat_solve,
    "json_patch": B.grade_json_patch,
    "unify": B.grade_unify,
    "mini_sql": B.grade_sql,
}

# Per-task scores from results/pyhard_failure_autopsy.md + session notes (gpt-oss).
AUTOPSY_OVERRIDES: dict[str, dict[str, tuple[int, int]]] = {
    "qwen3-coder-next_q8_0_pyhard_pyhard_latest.json": {
        "unify": (7, 10),
        "mini_sql": (0, 8),
    },
    "qwen3-coder_30b-a3b-fp16_pyhard_pyhard_latest.json": {
        "sat_solve": (7, 10),
        "mini_sql": (4, 8),
    },
    "gpt-oss_120b_pyhard_pyhard_latest.json": {
        "sat_solve": (6, 10),
        "mini_sql": (5, 8),
    },
}


def _find_text(stem: str, task: str) -> tuple[str | None, str]:
    candidates = [stem]
    if stem.endswith("_pyhard"):
        candidates.append(stem[: -len("_pyhard")])
    for pref in candidates:
        for path in (OUT / f"{pref}__{task}__code.py", OUT / f"{pref}__{task}.txt"):
            if path.is_file():
                return path.read_text(encoding="utf-8"), f"artifact:{path.name}"
    return None, "unavailable"


def rescore_file(path: Path) -> Path:
    rows = json.loads(path.read_text(encoding="utf-8"))
    stem = path.name.replace("_pyhard_latest.json", "")
    overrides = AUTOPSY_OVERRIDES.get(path.name, {})
    out_rows: list[dict] = []
    for row in rows:
        task = row["task"]
        entry = dict(row)
        entry["score_before"] = row["score"]
        text, source = _find_text(stem, task)
        if text is not None:
            if "```" not in text:
                text = f"```python\n{text}\n```"
            g = GRADERS[task](text)
            entry["score"] = int(g["score"])
            entry["max_score"] = int(g["max_score"])
            entry["ok"] = bool(g["ok"])
            entry["grade_detail"] = g.get("detail")
            entry["rescore_source"] = source
        elif task in overrides:
            sc, mx = overrides[task]
            entry["score"] = sc
            entry["max_score"] = mx
            entry["ok"] = sc == mx
            entry["rescore_source"] = "autopsy"
        elif row["score"] == row["max_score"]:
            entry["rescore_source"] = "official_pass"
        else:
            entry["rescore_source"] = "unavailable"
        out_rows.append(entry)
    out = path.with_name(path.name.replace("_pyhard_latest.json", "_pyhard_rescored_latest.json"))
    out.write_text(json.dumps(out_rows, indent=2) + "\n", encoding="utf-8")
    return out


def main() -> int:
    paths = [
        p
        for p in sorted(OUT.glob("*pyhard*latest.json"))
        if "rescored" not in p.name
    ]
    print(f"{'file':<64} {'before':>10} {'after':>10}  notes")
    for path in paths:
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list) or not rows or "task" not in rows[0]:
            continue
        before = sum(int(r["score"]) for r in rows)
        out = rescore_file(path)
        after_rows = json.loads(out.read_text(encoding="utf-8"))
        after = sum(int(r["score"]) for r in after_rows)
        mx = sum(int(r["max_score"]) for r in after_rows)
        un = [r["task"] for r in after_rows if r.get("rescore_source") == "unavailable"]
        note = f"unavailable={un}" if un else "ok"
        print(f"{path.name:<64} {before:>3}/{mx:<3}    {after:>3}/{mx:<3}  {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
