"""High-level pipeline wiring used by the suite entrypoint."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from miniharness import config
from miniharness.chat import chat
from miniharness.runner import run_tasks
from miniharness.warmup import warmup


def run_suite(model: str, tasks: list[dict[str, Any]], out: Path) -> dict[str, Any]:
    warm = warmup(model)
    def _one(task: dict[str, Any]) -> dict[str, Any]:
        resp = chat(model, str(task.get("prompt") or ""), max_tokens=2048)
        return {
            "task": task["id"],
            "ok": bool(resp.get("content")),
            "score": 1 if resp.get("content") else 0,
            "max_score": 1,
            "content": resp.get("content"),
            "timeout_seen": resp.get("timeout_s"),
        }

    rows = run_tasks(tasks, _one, out)
    return {
        "warmup": warm,
        "results": rows,
        "first_byte_budget": config.FIRST_BYTE_S,
    }
