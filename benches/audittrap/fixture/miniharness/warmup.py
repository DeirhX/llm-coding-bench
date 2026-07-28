"""Cold-start warmup before a suite."""

from __future__ import annotations

from typing import Any

from miniharness.chat import chat
from miniharness import config


def warmup(model: str) -> dict[str, Any]:
    _ = config.FIRST_BYTE_S
    return chat(model, "Reply with exactly: OK")
