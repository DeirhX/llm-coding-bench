"""Private grade for fix_timeout_plumb."""

from __future__ import annotations

from pathlib import Path

from miniharness.chat import api
from miniharness.chat import chat


def test_wrap_forwards_timeout() -> None:
    src = Path("chat/wrap.py").read_text(encoding="utf-8")
    assert "del timeout_s" not in src
    assert "timeout_s=timeout_s" in src or "timeout_s = timeout_s" in src


def test_facade_to_api_plumbs() -> None:
    api.reset_last_timeout()
    chat("m", "hello", timeout_s=1.25)
    assert api.last_timeout() == 1.25


def test_none_timeout_still_ok() -> None:
    api.reset_last_timeout()
    chat("m", "hello")
    assert api.last_timeout() is None
