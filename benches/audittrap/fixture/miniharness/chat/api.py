"""HTTP chat client (lowest layer)."""

from __future__ import annotations

from typing import Any

_LAST_TIMEOUT: float | None = None


def reset_last_timeout() -> None:
    global _LAST_TIMEOUT
    _LAST_TIMEOUT = None


def last_timeout() -> float | None:
    return _LAST_TIMEOUT


def post_chat(
    model: str,
    messages: list[dict[str, str]],
    *,
    timeout_s: float | None = None,
    temperature: float = 0.1,
    max_tokens: int = 1024,
) -> dict[str, Any]:
    global _LAST_TIMEOUT
    _LAST_TIMEOUT = timeout_s
    user = ""
    for m in messages:
        if m.get("role") == "user":
            user = m.get("content") or ""
    return {
        "content": f"OK:{model}:{user[:40]}",
        "thinking": "",
        "timeout_s": timeout_s,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "prompt_tokens": max(1, len(user) // 4),
        "eval_tokens": 3,
        "wall_s": 0.01,
        "done_reason": "stop",
    }
