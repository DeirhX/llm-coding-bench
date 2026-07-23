"""Ollama thinking / num_predict helpers shared by pyhard, arch, claim.

Thinking tokens share the same ``num_predict`` budget as answer tokens. Running
think-on at the default 16k often yields ``done_reason=length`` with empty
``content``. Prefer an explicit think level + a larger predict default.
"""

from __future__ import annotations

import os
from typing import Any


def parse_think(raw: str | None = None) -> bool | str:
    """Return a value for the top-level Ollama ``think`` field.

    Env ``BENCH_THINK``:
      - ``0`` / ``false`` / ``off`` → ``False``
      - ``1`` / ``true`` / ``on`` → ``True``, or ``BENCH_THINK_LEVEL`` if set
      - ``low`` / ``medium`` / ``high`` / ``max`` → that level string
    """
    v = (raw if raw is not None else os.environ.get("BENCH_THINK", "0")).strip().lower()
    if v in ("", "0", "false", "off", "no"):
        return False
    if v in ("1", "true", "on", "yes"):
        level = os.environ.get("BENCH_THINK_LEVEL", "").strip().lower()
        if level in ("low", "medium", "high", "max"):
            return level
        return True
    if v in ("low", "medium", "high", "max"):
        return v
    raise SystemExit(
        f"Invalid BENCH_THINK={v!r} (use 0|1|true|false|low|medium|high|max)"
    )


def thinking_enabled(think: bool | str | None = None) -> bool:
    t = parse_think() if think is None else think
    return t is not False


def default_num_predict(base: int, think_base: int | None = None) -> int:
    """Pick num_predict: explicit env wins; else raise default when thinking on."""
    if "BENCH_NUM_PREDICT" in os.environ:
        return int(os.environ["BENCH_NUM_PREDICT"])
    if thinking_enabled():
        return int(think_base if think_base is not None else max(base * 3, 49152))
    return base


def apply_think(body: dict[str, Any], think: bool | str | None = None) -> dict[str, Any]:
    """Set top-level ``think`` on an Ollama chat/generate body (never in options)."""
    t = parse_think() if think is None else think
    body["think"] = t
    return body


def grade_from_response(content: str, thinking: str, *, scrape_thinking: bool = False) -> str:
    """Text to grade: prefer answer ``content``; do not mine truncated thinking."""
    content = content or ""
    thinking = thinking or ""
    if content.strip():
        return content
    if scrape_thinking and thinking.strip():
        return thinking
    # Empty content with a think trace usually means num_predict exhaustion.
    return content
