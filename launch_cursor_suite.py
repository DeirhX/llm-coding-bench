#!/usr/bin/env python3.14
"""Detach run_cursor_suite.sh into its own session (survives parent shell death)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "run_cursor_suite.sh"
PID_FILE = ROOT / "results" / "cursor_composer25_suite.pid"


def main() -> int:
    if not SCRIPT.is_file():
        print(f"missing {SCRIPT}", file=sys.stderr)
        return 1
    (ROOT / "results").mkdir(parents=True, exist_ok=True)
    # Avoid double-start
    if PID_FILE.exists():
        try:
            old = int(PID_FILE.read_text().strip())
            os.kill(old, 0)
            print(f"already running pid={old}")
            return 0
        except (ValueError, OSError):
            pass
    env = os.environ.copy()
    env["PATH"] = str(Path.home() / ".local/bin") + ":/usr/local/bin:/usr/bin:/bin:" + env.get(
        "PATH", ""
    )
    proc = subprocess.Popen(
        ["/bin/zsh", str(SCRIPT)],
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid))
    print(f"started pid={proc.pid} log={ROOT / 'results' / 'cursor_composer25_suite.log'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
