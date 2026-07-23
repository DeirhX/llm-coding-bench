"""Per-task wall-clock budget for bench runners.

Default 600s. Override with ``BENCH_TASK_TIMEOUT_S``. Cursor Agent calls also
honor ``BENCH_CURSOR_TIMEOUT`` (defaults to the same budget).
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def task_timeout_s() -> float:
    return float(os.environ.get("BENCH_TASK_TIMEOUT_S", "600"))


def cursor_timeout_s() -> float:
    """Cursor CLI subprocess timeout — defaults to the per-task budget."""
    if "BENCH_CURSOR_TIMEOUT" in os.environ:
        return float(os.environ["BENCH_CURSOR_TIMEOUT"])
    return task_timeout_s()


class TaskTimeout(Exception):
    """Raised when a single bench task exceeds ``BENCH_TASK_TIMEOUT_S``."""


def timeout_result(
    *,
    model: str,
    provider: str,
    task_id: str,
    title: str,
    max_score: int,
    wall_s: float,
    detail: str = "TIMEOUT: exceeded BENCH_TASK_TIMEOUT_S",
    **extra: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "model": model,
        "provider": provider,
        "task": task_id,
        "title": title,
        "ok": False,
        "score": 0,
        "max_score": max_score,
        "grade_detail": detail,
        "wall_s": round(wall_s, 2),
        "done_reason": "task_timeout",
    }
    row.update(extra)
    return row


def call_with_deadline(
    fn: Callable[[], T],
    deadline: float,
    *,
    label: str = "task",
) -> T:
    """Run ``fn``; if wall clock already past ``deadline``, raise TaskTimeout.

    Cooperative helper for multi-round loops — call between rounds. Does not
    pre-empt a blocking call already in flight (Cursor/Ollama timeouts do that).
    """
    if time.perf_counter() >= deadline:
        raise TaskTimeout(f"{label} exceeded wall budget")
    return fn()
