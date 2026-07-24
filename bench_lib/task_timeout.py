"""Per-task wall-clock budget for bench runners.

Default 600s. Override with ``BENCH_TASK_TIMEOUT_S``. Cursor Agent calls also
honor ``BENCH_CURSOR_TIMEOUT`` (defaults to the same budget).
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def task_timeout_s() -> float:
    return float(os.environ.get("BENCH_TASK_TIMEOUT_S", "600"))


def cursor_timeout_s() -> float:
    """Cursor CLI subprocess timeout -- defaults to the per-task budget."""
    if "BENCH_CURSOR_TIMEOUT" in os.environ:
        return float(os.environ["BENCH_CURSOR_TIMEOUT"])
    return task_timeout_s()


class TaskTimeout(Exception):
    """Raised when a single bench task exceeds ``BENCH_TASK_TIMEOUT_S``."""


def timeout_result(
    *,
    model: str,
    provider: str,
    task_id: str,
    title: str,
    max_score: int,
    wall_s: float,
    detail: str = "TIMEOUT: exceeded BENCH_TASK_TIMEOUT_S",
    **extra: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "model": model,
        "provider": provider,
        "task": task_id,
        "title": title,
        "ok": False,
        "score": 0,
        "max_score": max_score,
        "grade_detail": detail,
        "wall_s": round(wall_s, 2),
        "done_reason": "task_timeout",
    }
    row.update(extra)
    return row


def call_with_deadline(
    fn: Callable[[], T],
    deadline: float,
    *,
    label: str = "task",
) -> T:
    """Run ``fn``; if wall clock already past ``deadline``, raise TaskTimeout.

    Cooperative helper for multi-round loops -- call between rounds. Does not
    pre-empt a blocking call already in flight (Cursor/Ollama timeouts do that).
    """
    if time.perf_counter() >= deadline:
        raise TaskTimeout(f"{label} exceeded wall budget")
    return fn()


def subprocess_with_hard_timeout(
    cmd: list[str],
    *,
    timeout_s: float | None = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with a hard wall-clock timeout that kills the process.

    Uses ``Popen`` directly so we can kill the entire process group on timeout
    (not just the parent process). ``subprocess.run(timeout=...)`` does not
    expose the ``Popen`` handle, so child processes are orphaned on timeout.
    """
    if timeout_s is None:
        timeout_s = task_timeout_s()

     # Ensure the process gets its own process group for clean killing
    if os.name != "nt":
        kwargs.setdefault("start_new_session", True)

    creationflags = kwargs.pop("creationflags", 0)
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
         **kwargs,
     )

    start = time.perf_counter()

    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        wall = time.perf_counter() - start
         # Kill the entire process group
        try:
            if os.name == "nt":
                os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
             # Process already exited or no process group - ignore
            pass
          # Drain pipes to prevent deadlock before wait()
        try:
            proc.stdout.read()
            proc.stderr.read()
        except (OSError, ValueError):
            pass
        proc.wait(timeout=10)

         # Use output captured by communicate() before timeout (avoids deadlock)
        stdout = e.stdout if e.stdout else ""
        stderr = e.stderr if e.stderr else ""

         # If we have partial output, return it with timeout marker
        if str(stdout).strip():
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=-9,
                stdout=str(stdout)[-2000:],
                stderr=f"[KILLED after {wall:.1f}s] {str(stderr)[-2000:]}",
             )

        raise TaskTimeout(
            f"subprocess exceeded {timeout_s:.1f}s wall-clock budget"
        ) from e
    else:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
         )
