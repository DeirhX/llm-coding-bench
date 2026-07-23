#!/usr/bin/env python3.14
"""Re-grade saved repohard result JSON with the current private tests.

Prefers ``answer.patch`` (full diff persisted by the harness). Falls back to
``raw_content`` / ``patch_preview`` for older result files.

Writes ``*_repohard_rescored_latest.json`` next to each input and prints a
before/after table.

Usage:
  python3.14 -m benches.repohard.rescore
  python3.14 -m benches.repohard.rescore results/repohard/cursor_composer-2.5_repohard_latest.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benches.repohard.bench import parse_final_answer  # noqa: E402
from benches.repohard.tasks import grade_patch  # noqa: E402
from benches.repohard.tools import ToolSession  # noqa: E402
from bench_lib.paths import results_dir  # noqa: E402


def _looks_like_patch(text: str) -> bool:
    return "--- " in text or "diff --git" in text


def _answer_from_row(row: dict[str, Any]) -> dict[str, Any]:
    ans = row.get("answer") or {}
    stored = ans.get("patch")
    if isinstance(stored, str) and _looks_like_patch(stored):
        return {"patch": stored}
    raw = row.get("raw_content") or ""
    final = parse_final_answer(raw) if raw else None
    if isinstance(final, dict) and (
        final.get("patch") or final.get("diff") or final.get("unified_diff")
    ):
        return final
    preview = ans.get("patch_preview")
    if isinstance(preview, str) and _looks_like_patch(preview):
        return {"patch": preview}
    return final or {}


def rescore_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or "task" not in row:
            out.append(row)
            continue
        task_id = str(row["task"])
        answer = _answer_from_row(row)
        session = ToolSession(max_calls=0)
        g = grade_patch(answer, session, task_id)
        new = dict(row)
        new["score_before"] = row.get("score")
        new["ok_before"] = row.get("ok")
        new["grade_detail_before"] = row.get("grade_detail")
        new["rescored"] = True

        detail = str(g.get("detail") or "")
        before_detail = str(row.get("grade_detail") or "")
        # Older results only kept raw_content[:8000] / patch_preview — truncated
        # patches cannot reproduce a previously successful apply. Keep official.
        has_full = isinstance((row.get("answer") or {}).get("patch"), str) and _looks_like_patch(
            str((row.get("answer") or {}).get("patch"))
        )
        if (
            not has_full
            and detail.startswith("patch_apply:")
            and before_detail.startswith("pytest")
        ):
            new["rescored_note"] = "kept_official: full patch unavailable (raw truncated)"
            new["score"] = int(row.get("score") or 0)
            new["max_score"] = int(row.get("max_score") or 10)
            new["ok"] = bool(row.get("ok"))
            new["grade_detail"] = before_detail
            new["passed"] = row.get("passed")
            new["total"] = row.get("total")
            out.append(new)
            continue

        new["score"] = int(g["score"])
        new["max_score"] = int(g.get("max_score") or 10)
        new["ok"] = bool(g.get("ok"))
        new["grade_detail"] = g.get("detail")
        new["passed"] = g.get("passed")
        new["total"] = g.get("total")
        if g.get("apply_detail"):
            ans = dict(new.get("answer") or {})
            ans["apply_detail"] = g.get("apply_detail")
            ans["patch_bytes"] = g.get("patch_bytes")
            new["answer"] = ans
        out.append(new)
    return out


def totals(rows: list[dict[str, Any]]) -> tuple[int, int]:
    return (
        sum(int(r.get("score") or 0) for r in rows if isinstance(r, dict)),
        sum(int(r.get("max_score") or 0) for r in rows if isinstance(r, dict)),
    )


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        paths = [Path(a) for a in argv[1:]]
    else:
        paths = sorted(results_dir("repohard").glob("*_repohard_latest.json"))
        # skip already-rescored inputs
        paths = [p for p in paths if "_rescored_" not in p.name]

    print(f"{'file':<62} {'before':>8} {'after':>8}")
    for path in paths:
        if not path.is_file():
            print(f"missing {path}", file=sys.stderr)
            continue
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj, list):
            print(f"skip {path.name} (not a task list)")
            continue
        if len(obj) < 8:
            print(f"skip {path.name} (incomplete {len(obj)}/8)")
            continue
        before_s, before_m = totals(obj)
        rescored = rescore_rows(obj)
        after_s, after_m = totals(rescored)
        if path.name.endswith("_repohard_latest.json"):
            out = path.with_name(
                path.name.replace("_repohard_latest.json", "_repohard_rescored_latest.json")
            )
        else:
            out = path.with_name(path.stem + "_rescored.json")
        out.write_text(json.dumps(rescored, indent=2) + "\n", encoding="utf-8")
        delta = after_s - before_s
        flag = f"  ({delta:+d})" if delta else ""
        print(f"{path.name:<62} {before_s:>3}/{before_m:<3}  {after_s:>3}/{after_m:<3}{flag}")
        # per-task deltas
        for old, new in zip(obj, rescored):
            if not isinstance(old, dict) or not isinstance(new, dict):
                continue
            if int(old.get("score") or 0) != int(new.get("score") or 0):
                print(
                    f"    {new.get('task')}: {old.get('score')} → {new.get('score')}  "
                    f"({new.get('grade_detail')})"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
