#!/usr/bin/env python3.14
"""Kill a hung ``run.py`` child when the current ``-- task`` line is older than budget.

Watches log files for lines starting with ``-- <task> ...``. If the log tip is
still that task banner (or no progress) and the log mtime is older than
``BENCH_TASK_TIMEOUT_S`` (default 600), sends SIGTERM to the matching ``run.py``
PID so the parent queue can continue.

Usage:
  python3.14 scripts/watch_stuck_tasks.py \\
    --budget 600 results/think_improved.log results/cursor_gap_queue.log
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

TASK_RE = re.compile(r"^-- ([A-Za-z0-9_]+) \.\.\.")
DONE_RE = re.compile(r"^(==== |---- |SUMMARY)")


def timeout_s(cli: float | None = None) -> float:
    if cli is not None:
        return float(cli)
    return float(os.environ.get("BENCH_TASK_TIMEOUT_S", "600"))


def pids_running_run_py() -> list[tuple[int, str]]:
    out = subprocess.check_output(["ps", "-ax", "-o", "pid=,command="], text=True)
    hits: list[tuple[int, str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_s, _, cmd = line.partition(" ")
        if "run.py" in cmd and "run " in cmd and "watch_stuck" not in cmd:
            try:
                hits.append((int(pid_s), cmd))
            except ValueError:
                continue
    return hits


def last_task(log: Path) -> tuple[str | None, float | None]:
    """Return (task, idle_anchor_ts).

    idle_anchor_ts is log mtime when the tip is still the task banner (stuck /
    no further writes). Otherwise first_seen wall time is used by the caller.
    """
    if not log.is_file():
        return None, None
    text = log.read_text(encoding="utf-8", errors="replace")
    task = None
    for line in text.splitlines():
        m = TASK_RE.match(line)
        if m:
            task = m.group(1)
        elif DONE_RE.match(line) and task:
            if line.startswith("==== ") or line.startswith("---- "):
                task = None
    if task is None:
        return None, None
    tip = text.rstrip().splitlines()
    mtime = log.stat().st_mtime
    if tip and TASK_RE.match(tip[-1]):
        # No progress since the banner — mtime is the idle clock.
        return task, mtime
    return task, None


def _append_timeout_row(latest: Path, task: str, max_score: int = 10) -> None:
    rows: list = []
    if latest.is_file():
        try:
            rows = json.loads(latest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            rows = []
    if not isinstance(rows, list):
        rows = []
    rows = [r for r in rows if not (isinstance(r, dict) and r.get("task") == task)]
    rows.append(
        {
            "task": task,
            "ok": False,
            "score": 0,
            "max_score": max_score,
            "grade_detail": "TIMEOUT: exceeded BENCH_TASK_TIMEOUT_S (watchdog)",
            "done_reason": "task_timeout",
            "wall_s": timeout_s(),
        }
    )
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"wrote timeout stub task={task} -> {latest}", flush=True)


def _latest_for_log(log: Path, cmd: str) -> Path | None:
    """Map a hung run.py + log to the results file that should get a timeout stub."""
    if log.name == "think_improved.log" or "arch_think" in cmd or "run arch" in cmd:
        tail = log.read_text(encoding="utf-8", errors="replace")[-4000:]
        if "qwen3.6" in cmd or "qwen3.6" in tail:
            return Path("results/archbench/qwen3.6_35b-a3b-coding-bf16_arch_think_latest.json")
        return Path("results/archbench/qwen3.5_35b-a3b-coding-bf16_arch_think_latest.json")
    if "repohard" in cmd or log.name == "cursor_gap_queue.log":
        tip = log.read_text(encoding="utf-8", errors="replace")[-4000:]
        m = re.search(r"model=([^\s]+).*bench=repohard", tip)
        model = None
        if m:
            model = m.group(1)
        if not model:
            m2 = re.search(
                r"BENCH_MODEL=([^\s]+)|model=(gemini-3\.6-flash-high|cursor-grok[^ ]+|gpt-[^ ]+|claude-[^ ]+|composer-[^ ]+)",
                tip,
            )
            if m2:
                model = m2.group(1) or m2.group(2)
        if model:
            safe = re.sub(r"[^a-zA-Z0-9._-]", "_", model)
            return Path("results/repohard") / f"cursor_{safe}_repohard_latest.json"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--budget",
        type=float,
        default=None,
        help="seconds before kill (default: BENCH_TASK_TIMEOUT_S or 600)",
    )
    ap.add_argument(
        "logs",
        nargs="*",
        type=Path,
        help="log files to watch",
    )
    args = ap.parse_args()
    logs = args.logs or [
        Path("results/think_improved.log"),
        Path("results/cursor_gap_queue.log"),
    ]
    budget = timeout_s(args.budget)
    print(f"watchdog start budget={budget}s logs={[str(p) for p in logs]}", flush=True)
    # key -> (task, started_wall)
    seen: dict[str, tuple[str, float]] = {}
    while True:
        for log in logs:
            task, idle_ts = last_task(log)
            key = str(log)
            if task is None:
                seen.pop(key, None)
                continue
            prev = seen.get(key)
            if prev is None or prev[0] != task:
                started = idle_ts if idle_ts is not None else time.time()
                seen[key] = (task, started)
                print(
                    f"watch {log.name}: tracking task={task} age0={time.time()-started:.0f}s",
                    flush=True,
                )
                continue
            # Refresh idle clock from mtime when tip is still the banner.
            if idle_ts is not None:
                seen[key] = (task, idle_ts)
            started = seen[key][1]
            age = time.time() - started
            if age < budget:
                continue
            victims = pids_running_run_py()
            # Only kill the suite that owns this log — never "all run.py".
            if "think" in log.name:
                matched = [v for v in victims if "run arch" in v[1] or "run pyhard" in v[1]]
            elif "gap" in log.name:
                # Cursor gap: provider is cursor; avoid killing local ollama repohard.
                matched = [
                    v
                    for v in victims
                    if "run repohard" in v[1]
                    or "run arch" in v[1]
                    or "run pyhard" in v[1]
                    or "run claim" in v[1]
                ]
                # If both ollama + cursor repohard run, prefer the one whose
                # parent log is actively being written (gap). Heuristic: kill
                # none here if we cannot tell — Cursor timeout handles Cursor.
                cursorish = []
                for pid, cmd in matched:
                    try:
                        env = Path(f"/proc/{pid}/environ").read_bytes()
                    except OSError:
                        env = b""
                    # macOS has no /proc; fall back to recent gap log activity.
                    if b"BENCH_PROVIDER=cursor" in env:
                        cursorish.append((pid, cmd))
                if cursorish:
                    matched = cursorish
                else:
                    # macOS: only kill if a single matching victim exists.
                    if len(matched) != 1:
                        print(
                            f"watch {log.name}: {task} age={age:.0f}s "
                            f"ambiguous victims={len(matched)}; skip kill",
                            flush=True,
                        )
                        continue
            else:
                matched = []
            if not matched:
                print(
                    f"watch {log.name}: {task} age={age:.0f}s but no matching run.py "
                    f"(stale banner?)",
                    flush=True,
                )
                # Advance start so a dead banner does not spam forever.
                seen[key] = (task, time.time())
                continue
            for pid, cmd in matched:
                print(
                    f"WATCHDOG TIMEOUT task={task} age={age:.0f}s kill pid={pid} cmd={cmd[:120]}",
                    flush=True,
                )
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError as e:
                    print(f"kill failed: {e}", flush=True)
                latest = _latest_for_log(log, cmd)
                if latest is not None:
                    _append_timeout_row(latest, task)
            # Avoid re-killing the same stuck banner every 15s.
            seen[key] = (task, time.time() + budget)
        time.sleep(15)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
