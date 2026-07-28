"""Subprocess helper with hard wall timeout."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Any


class ProcTimeout(TimeoutError):
    pass


def subprocess_with_hard_timeout(
    cmd: list[str],
    *,
    timeout_s: float,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    if os.name != "nt":
        kwargs.setdefault("start_new_session", True)

    creationflags = kwargs.pop("creationflags", 0)
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

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
        try:
            if os.name == "nt":
                os.kill(proc.pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        try:
            if proc.stdout:
                proc.stdout.read()
            if proc.stderr:
                proc.stderr.read()
        except (OSError, ValueError):
            pass
        proc.wait(timeout=10)
        stdout = e.stdout if e.stdout else ""
        stderr = e.stderr if e.stderr else ""
        if str(stdout).strip():
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=-9,
                stdout=str(stdout)[-2000:],
                stderr=f"[KILLED after {wall:.1f}s] {str(stderr)[-2000:]}",
            )
        raise ProcTimeout(f"subprocess exceeded {timeout_s:.1f}s") from e
    return subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
    )
