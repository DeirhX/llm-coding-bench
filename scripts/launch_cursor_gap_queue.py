#!/usr/bin/env python3.14
"""Detach run_cursor_gap_queue.sh into its own session.

Usage:
  python3.14 scripts/launch_cursor_gap_queue.py
  python3.14 scripts/launch_cursor_gap_queue.py --from gpt-5.6-sol-high
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
SCRIPT = SCRIPTS / "run_cursor_gap_queue.sh"
PID_FILE = ROOT / "results" / "cursor_gap_queue.pid"
LOG = ROOT / "results" / "cursor_gap_queue.log"


def main() -> int:
    if not SCRIPT.is_file():
        print(f"missing {SCRIPT}", file=sys.stderr)
        return 1
    (ROOT / "results").mkdir(parents=True, exist_ok=True)
    if PID_FILE.exists():
        try:
            old = int(PID_FILE.read_text().strip())
            os.kill(old, 0)
            print(f"already running pid={old} log={LOG}")
            return 0
        except (ValueError, OSError):
            pass
    env = os.environ.copy()
    env["PATH"] = (
        str(Path.home() / ".local/bin")
        + ":/usr/local/bin:/usr/bin:/bin:"
        + env.get("PATH", "")
    )
    env.setdefault("BENCH_OUT", str(ROOT / "results"))
    LOG.touch(exist_ok=True)
    cmd = ["/bin/zsh", str(SCRIPT), *sys.argv[1:]]
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    print(f"started pid={proc.pid} log={LOG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
