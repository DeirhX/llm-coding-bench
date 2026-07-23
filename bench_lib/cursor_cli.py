"""Invoke Cursor Agent CLI (`agent`) as a model backend.

Uses headless print mode:
  agent -p --output-format json --mode ask --model <id> --trust [--workspace DIR]

Requires `agent` on PATH (https://cursor.com/install) and an authenticated account
(`agent login` / `agent status`).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


class CursorCLIError(RuntimeError):
    pass


def find_agent() -> str:
    override = os.environ.get("CURSOR_AGENT_BIN", "").strip()
    if override:
        return override
    found = shutil.which("agent")
    if not found:
        raise CursorCLIError(
            "Cursor CLI `agent` not found on PATH. Install via: "
            "curl https://cursor.com/install -fsS | bash"
        )
    return found


def list_models() -> list[tuple[str, str]]:
    """Return [(id, display_name), ...] from `agent models`."""
    agent = find_agent()
    proc = subprocess.run(
        [agent, "models"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise CursorCLIError(proc.stderr.strip() or proc.stdout.strip() or "agent models failed")
    rows: list[tuple[str, str]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("Available") or line.startswith("Tip:"):
            continue
        if " - " not in line:
            continue
        mid, name = line.split(" - ", 1)
        rows.append((mid.strip(), name.strip()))
    return rows


def _parse_json_payload(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    if not text:
        raise CursorCLIError("empty stdout from agent")
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    # Tolerate leading/trailing log noise: decode every top-level object.
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    i = 0
    while i < len(text):
        if text[i] != "{":
            i += 1
            continue
        try:
            data, end = decoder.raw_decode(text[i:])
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(data, dict):
            candidates.append(data)
        i += max(end, 1)
    for data in reversed(candidates):
        if data.get("type") == "result" or "result" in data or "usage" in data:
            return data
    if candidates:
        return candidates[-1]
    raise CursorCLIError(f"non-JSON agent output: {text[:400]!r}")


def chat(
    model: str,
    prompt: str,
    *,
    mode: str | None = None,
    workspace: str | Path | None = None,
    force: bool | None = None,
    timeout_s: float | None = None,
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    """One-shot Cursor Agent invocation. Returns a normalized chat-like dict."""
    agent = find_agent()
    mode = mode or os.environ.get("BENCH_CURSOR_MODE", "ask")
    timeout_s = float(
        timeout_s
        if timeout_s is not None
        else os.environ.get("BENCH_CURSOR_TIMEOUT", "1800")
    )
    if force is None:
        force = os.environ.get("BENCH_CURSOR_FORCE", "0") == "1"

    cmd = [
        agent,
        "-p",
        "--output-format",
        "json",
        "--mode",
        mode,
        "--model",
        model,
        "--trust",
    ]
    if force:
        cmd.append("--force")
    if workspace is not None:
        cmd.extend(["--workspace", str(workspace)])
    if extra_args:
        cmd.extend(extra_args)

    env = os.environ.copy()
    # Prefer a quiet non-interactive agent
    env.setdefault("NO_OPEN_BROWSER", "1")

    t0 = time.perf_counter()
    proc = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        env=env,
    )
    wall = time.perf_counter() - t0

    if proc.returncode != 0 and not proc.stdout.strip():
        raise CursorCLIError(
            f"agent exit {proc.returncode}: {(proc.stderr or proc.stdout or '').strip()[:800]}"
        )

    data = _parse_json_payload(proc.stdout)
    if data.get("is_error"):
        raise CursorCLIError(
            f"agent error result: {data.get('result') or data.get('subtype') or data}"
        )

    result = data.get("result")
    if result is None:
        result = ""
    if not isinstance(result, str):
        result = json.dumps(result)

    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    prompt_tokens = int(usage.get("inputTokens") or usage.get("promptTokens") or 0)
    eval_tokens = int(usage.get("outputTokens") or usage.get("completionTokens") or 0)
    cache_read = int(usage.get("cacheReadTokens") or 0)
    cache_write = int(usage.get("cacheWriteTokens") or 0)
    duration_api_ms = float(data.get("duration_api_ms") or data.get("duration_ms") or 0)
    toks_per_s = (eval_tokens / (duration_api_ms / 1000.0)) if duration_api_ms > 0 else 0.0

    return {
        "content": result,
        "thinking": "",
        "combined": result,
        "wall_s": wall,
        "load_s": 0.0,
        "prompt_tokens": prompt_tokens,
        "eval_tokens": eval_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "toks_per_s": toks_per_s,
        "done_reason": data.get("subtype") or "stop",
        "session_id": data.get("session_id"),
        "request_id": data.get("request_id"),
        "provider": "cursor",
        "raw": data,
        "stderr": (proc.stderr or "")[-2000:],
    }
