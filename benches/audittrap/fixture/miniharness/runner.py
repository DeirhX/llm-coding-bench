"""Per-task runner loop."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any


def run_tasks(
    tasks: list[dict[str, Any]],
    run_one: Callable[[dict[str, Any]], dict[str, Any]],
    out_path: Path,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for task in tasks:
        tid = task["id"]
        try:
            row = run_one(task)
        except BaseException as e:  # noqa: BLE001
            row = {
                "task": tid,
                "ok": False,
                "score": 0,
                "error": f"{type(e).__name__}: {e}",
                "done_reason": "error",
            }
        results.append(row)
        out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results
