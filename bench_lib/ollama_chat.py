"""Robust Ollama /api/chat helper for long think-on benches.

Deadlock we hit: default keep-alive expires mid-generation → runner stuck in
``Stopping...`` while a non-streaming client blocks forever on one TCP read.

Mitigations:
  1. ``keep_alive=-1`` (indefinite) unless overridden via ``BENCH_KEEP_ALIVE``
  2. NDJSON streaming with a per-read socket timeout (stall → abort + retry)
  3. Preflight unload if ``/api/ps`` still lists an expired keep-alive entry
"""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

from bench_lib.ollama_think import apply_keep_alive, apply_think, parse_think


def _host() -> str:
    return os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def _stall_s() -> float:
    # Max idle time between stream chunks before we declare the runner wedged.
    return float(os.environ.get("BENCH_STREAM_STALL_S", "180"))


def _first_byte_s() -> float:
    # Prefill on large ctx / cold load can exceed the inter-token stall window.
    return float(os.environ.get("BENCH_FIRST_BYTE_S", "600"))


def _chat_retries() -> int:
    return int(os.environ.get("BENCH_OLLAMA_RETRIES", "2"))


def model_ps(model: str) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(f"{_host()}/api/ps", timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return None
    for m in data.get("models") or []:
        if m.get("name") == model or m.get("model") == model:
            return m
    return None


def _expires_past(expires_at: str) -> bool:
    if not expires_at:
        return False
    try:
        # e.g. 2026-07-23T15:01:20.796796+02:00
        dt = datetime.fromisoformat(expires_at)
        return dt.timestamp() < time.time() - 5
    except Exception:
        return False


def force_unload(model: str) -> None:
    try:
        req = urllib.request.Request(
            f"{_host()}/api/generate",
            data=json.dumps({"model": model, "keep_alive": 0}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp.read()
    except Exception:
        pass


def wait_model_ready(model: str, timeout_s: float = 180.0) -> None:
    """Clear expired/Stopping model entries so the next chat can load cleanly."""
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        m = model_ps(model)
        if m is None:
            return
        if _expires_past(str(m.get("expires_at") or "")):
            force_unload(model)
            time.sleep(2)
            continue
        return
    force_unload(model)


class StreamStallError(TimeoutError):
    """No stream tokens arrived within the stall window."""


def _read_stream(resp: Any) -> dict[str, Any]:
    """Consume NDJSON chat stream. Socket timeout on resp provides stall detection."""
    content_parts: list[str] = []
    thinking_parts: list[str] = []
    buf = b""
    while True:
        try:
            chunk = resp.read(4096)
        except (TimeoutError, socket.timeout) as e:
            raise StreamStallError("stream stall (socket timeout)") from e
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            msg = obj.get("message") or {}
            if msg.get("content"):
                content_parts.append(msg["content"])
            if msg.get("thinking"):
                thinking_parts.append(msg["thinking"])
            if obj.get("done"):
                obj["message"] = {
                    "role": "assistant",
                    "content": "".join(content_parts),
                    "thinking": "".join(thinking_parts),
                }
                return obj
    return {
        "message": {
            "role": "assistant",
            "content": "".join(content_parts),
            "thinking": "".join(thinking_parts),
        },
        "done": True,
        "done_reason": "incomplete_stream",
    }


def chat(
    model: str,
    messages: list[dict[str, str]],
    *,
    options: dict[str, Any] | None = None,
    think: bool | str | None = None,
    stream: bool | None = None,
) -> dict[str, Any]:
    """Chat with deadlock-avoidance. Returns normalized timing fields."""
    use_stream = (
        (os.environ.get("BENCH_OLLAMA_STREAM", "1") != "0")
        if stream is None
        else stream
    )
    think_val = parse_think() if think is None else think
    stall = _stall_s()
    retries = max(1, _chat_retries())
    last_err: Exception | None = None

    for attempt in range(1, retries + 1):
        wait_model_ready(model)
        body: dict[str, Any] = {
            "model": model,
            "stream": use_stream,
            "messages": messages,
            "options": options or {},
        }
        apply_think(body, think_val)
        apply_keep_alive(body, default="-1")
        data_bytes = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{_host()}/api/chat",
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        t0 = time.perf_counter()
        # First-byte budget covers cold load + prefill; then tighten to stall.
        connect_timeout = _first_byte_s() if use_stream else 3600.0
        try:
            with urllib.request.urlopen(req, timeout=connect_timeout) as resp:
                if use_stream:
                    try:
                        sock = resp.fp.raw._sock  # type: ignore[attr-defined]
                        sock.settimeout(stall)
                    except Exception:
                        pass
                    data = _read_stream(resp)
                else:
                    data = json.loads(resp.read().decode())
        except StreamStallError as e:
            last_err = e
            force_unload(model)
            time.sleep(min(30, 2**attempt))
            continue
        except (TimeoutError, socket.timeout) as e:
            last_err = StreamStallError(str(e))
            force_unload(model)
            time.sleep(min(30, 2**attempt))
            continue
        except urllib.error.HTTPError as e:
            last_err = e
            err_body = e.read().decode("utf-8", errors="replace")
            if e.code == 500 and "EOF" in err_body and attempt >= 2:
                break
            time.sleep(min(30, 2**attempt))
            continue
        except (urllib.error.URLError, ConnectionError) as e:
            last_err = e
            time.sleep(min(30, 2**attempt))
            continue

        wall = time.perf_counter() - t0
        msg = data.get("message") or {}
        content = msg.get("content") or ""
        thinking = msg.get("thinking") or ""
        eval_duration = float(data.get("eval_duration") or 0)
        eval_count = float(data.get("eval_count") or 0)
        return {
            "content": content,
            "thinking": thinking,
            "wall_s": wall,
            "load_s": float(data.get("load_duration") or 0) / 1e9,
            "prompt_tokens": int(data.get("prompt_eval_count") or 0),
            "eval_tokens": int(data.get("eval_count") or 0),
            "toks_per_s": (eval_count / (eval_duration / 1e9)) if eval_duration > 0 else 0.0,
            "done_reason": data.get("done_reason"),
            "raw": data,
            "think": think_val,
            "stream": use_stream,
            "attempt": attempt,
        }

    raise last_err or RuntimeError("ollama chat failed")
