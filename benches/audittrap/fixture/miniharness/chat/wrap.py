"""Mid-layer chat wrapper."""

from __future__ import annotations

from typing import Any

from miniharness.chat import api


def chat_wrapped(
    model: str,
    messages: list[dict[str, str]],
    *,
    timeout_s: float | None = None,
    temperature: float = 0.1,
    max_tokens: int = 1024,
    retry: int = 1,
) -> dict[str, Any]:
    del timeout_s
    last: dict[str, Any] | None = None
    for _ in range(max(1, retry)):
        last = api.post_chat(
            model,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if last.get("content"):
            return last
    assert last is not None
    return last
