"""Public chat facade used by warmup and tasks."""

from __future__ import annotations

from typing import Any

from miniharness.chat import wrap


def chat(
    model: str,
    prompt: str,
    *,
    timeout_s: float | None = None,
    temperature: float = 0.1,
    max_tokens: int = 1024,
) -> dict[str, Any]:
    messages = [{"role": "user", "content": prompt}]
    return wrap.chat_wrapped(
        model,
        messages,
        timeout_s=timeout_s,
        temperature=temperature,
        max_tokens=max_tokens,
    )
