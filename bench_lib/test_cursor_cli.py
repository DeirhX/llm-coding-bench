#!/usr/bin/env python3.14
"""Offline unit checks for Cursor CLI JSON parsing (no network)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from bench_lib.cursor_cli import CursorCLIError, _parse_json_payload  # noqa: E402


def main() -> int:
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "```python\ndef add(a, b):\n    return a + b\n```",
        "usage": {"inputTokens": 10, "outputTokens": 5},
    }
    raw = "noise\n" + json.dumps(payload)
    got = _parse_json_payload(raw)
    assert got["result"].startswith("```python")
    try:
        _parse_json_payload("")
        raise AssertionError("expected empty failure")
    except CursorCLIError:
        pass
    print("cursor_cli parser OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
