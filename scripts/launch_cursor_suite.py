#!/usr/bin/env python3.14
"""Detach run_cursor_suite.sh into its own session (survives parent shell death).

Usage:
  python3.14 scripts/launch_cursor_suite.py
  python3.14 scripts/launch_cursor_suite.py claude-sonnet-5-high
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
SCRIPT = SCRIPTS / "run_cursor_suite.sh"


def main() -> int:
    if not SCRIPT.is_file():
        print(f"missing {SCRIPT}", file=sys.stderr)
        return 1
    model = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BENCH_MODEL", "composer-2.5")
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", model)
    (ROOT / "results").mkdir(parents=True, exist_ok=True)
    pid_file = ROOT / "results" / f"cursor_{safe}_suite.pid"
    if pid_file.exists():
        try:
            old = int(pid_file.read_text().strip())
            os.kill(old, 0)
            print(f"already running model={model} pid={old}")
            return 0
        except (ValueError, OSError):
            pass
    env = os.environ.copy()
    env["PATH"] = str(Path.home() / ".local/bin") + ":/usr/local/bin:/usr/bin:/bin:" + env.get(
        "PATH", ""
    )
    env["BENCH_MODEL"] = model
    log = ROOT / "results" / f"cursor_{safe}_suite.log"
    # Touch before spawn so parallel `tail -f` / follow_log.sh cannot race.
    log.parent.mkdir(parents=True, exist_ok=True)
    log.touch(exist_ok=True)
    proc = subprocess.Popen(
        ["/bin/zsh", str(SCRIPT), model],
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pid_file.write_text(str(proc.pid), encoding="utf-8")
    print(f"started model={model} pid={proc.pid} log={log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
