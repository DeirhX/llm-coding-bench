"""Private grade for fix_warmup_timeout."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from miniharness import config
from miniharness import warmup as warmup_mod


def test_source_passes_short_timeout() -> None:
    src = Path("warmup.py").read_text(encoding="utf-8")
    assert "timeout_s" in src
    assert "DEFAULT_WARMUP_TIMEOUT_S" in src or "30" in src


def test_warmup_calls_chat_with_short_timeout() -> None:
    seen: dict[str, Any] = {}

    def fake_chat(model: str, prompt: str, **kwargs: Any) -> dict[str, Any]:
        seen["model"] = model
        seen["prompt"] = prompt
        seen.update(kwargs)
        return {"content": "OK"}

    with patch.object(warmup_mod, "chat", fake_chat):
        warmup_mod.warmup("m")
    assert "timeout_s" in seen
    assert float(seen["timeout_s"]) <= 60.0
    assert float(seen["timeout_s"]) == float(config.DEFAULT_WARMUP_TIMEOUT_S)
