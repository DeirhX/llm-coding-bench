"""Private grade for fix_stderr_kill."""

from __future__ import annotations

import sys
from pathlib import Path

from miniharness.util.subprocess_timeout import ProcTimeout, subprocess_with_hard_timeout


def test_source_accepts_stderr_only() -> None:
    src = Path("util/subprocess_timeout.py").read_text(encoding="utf-8")
    # Must not require stdout alone.
    assert "if str(stdout).strip():" not in src
    assert "stdout" in src and "stderr" in src


def test_stderr_only_returns_completed() -> None:
    code = (
        "import sys, time\n"
        "sys.stderr.write('boom-err\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep(30)\n"
    )
    r = subprocess_with_hard_timeout(
        [sys.executable, "-c", code],
        timeout_s=0.4,
    )
    assert r.returncode == -9
    assert "boom-err" in (r.stderr or "")
    assert "[KILLED" in (r.stderr or "")


def test_empty_both_still_raises() -> None:
    code = "import time; time.sleep(30)"
    try:
        subprocess_with_hard_timeout([sys.executable, "-c", code], timeout_s=0.3)
        raise AssertionError("expected ProcTimeout")
    except ProcTimeout:
        pass
