#!/usr/bin/env python3.14
"""Run arch / claim / repohard / audittrap against an OpenAI-compatible API (ds4-server).

Patches each suite's ``chat()`` to hit ``DS4_BASE/v1/chat/completions`` while
keeping ``BENCH_PROVIDER=ollama`` so existing multi-round tool loops run unchanged.

  DS4_BASE=http://127.0.0.1:8000 \\
  BENCH_MODEL=deepseek-v4-flash \\
  BENCH_THINK=0 \\
  python3.14 scripts/run_agent_benches_openai.py [arch|claim|repohard|audittrap ...]

Hang mitigations (vs the old non-streaming 8k wall-clock black hole):
  * SSE streaming with per-chunk idle timeout (``BENCH_STREAM_STALL_S``)
  * Per-call HTTP budget clamped to remaining task time (``timeout_s``)
  * Default ``BENCH_NUM_PREDICT=2048`` (was 8192)
"""
from __future__ import annotations

import importlib
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASE = os.environ.get("DS4_BASE", "http://127.0.0.1:8000").rstrip("/")
MODEL = os.environ.setdefault("BENCH_MODEL", "deepseek-v4-flash")
os.environ.setdefault("BENCH_PROVIDER", "ollama")
os.environ.setdefault("BENCH_THINK", "0")
os.environ.setdefault("BENCH_NUM_CTX", "65536")
# Cap completion length so agent rounds cannot burn the whole task budget on rants.
os.environ.setdefault("BENCH_NUM_PREDICT", "2048")
os.environ.setdefault("BENCH_TASK_TIMEOUT_S", "900")
# Hard ceiling for a single HTTP call when the agent does not pass timeout_s.
os.environ.setdefault("BENCH_HTTP_TIMEOUT_S", "300")
os.environ.setdefault("BENCH_STREAM_STALL_S", "120")
os.environ.setdefault("BENCH_FIRST_BYTE_S", "180")
os.environ.setdefault("BENCH_OPENAI_STREAM", "1")
# Avoid colliding with a parent BENCH_TAG meant for pyhard.
os.environ.pop("BENCH_TAG", None)

SUITES = ("arch", "claim", "repohard", "audittrap")


class StreamStallError(TimeoutError):
    """No SSE tokens arrived within the stall window."""

    def __init__(
        self,
        message: str,
        *,
        content: str = "",
        thinking: str = "",
        usage: dict[str, Any] | None = None,
        finish_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.content = content
        self.thinking = thinking
        self.usage = usage or {}
        self.finish_reason = finish_reason


def _stall_s() -> float:
    return float(os.environ.get("BENCH_STREAM_STALL_S", "120"))


def _first_byte_s() -> float:
    return float(os.environ.get("BENCH_FIRST_BYTE_S", "180"))


def _http_ceiling_s() -> float:
    return float(os.environ.get("BENCH_HTTP_TIMEOUT_S", "300"))


def _use_stream() -> bool:
    return os.environ.get("BENCH_OPENAI_STREAM", "1") not in ("0", "false", "off", "")


def _read_sse_stream(
    resp: Any,
    *,
    on_thinking=None,
    on_content=None,
    hard_deadline: float | None = None,
) -> dict[str, Any]:
    """Consume OpenAI-style SSE chat.completions stream."""
    content_parts: list[str] = []
    thinking_parts: list[str] = []
    finish_reason: str | None = None
    usage: dict[str, Any] = {}
    buf = b""

    def _partial() -> dict[str, Any]:
        return {
            "content": "".join(content_parts),
            "thinking": "".join(thinking_parts),
            "finish_reason": finish_reason,
            "usage": usage,
        }

    while True:
        if hard_deadline is not None and time.perf_counter() >= hard_deadline:
            raise StreamStallError(
                "task deadline hit mid-stream",
                **_partial(),
            )
        try:
            chunk = resp.read(4096)
        except (TimeoutError, socket.timeout) as e:
            raise StreamStallError(
                "stream stall (socket timeout)",
                **_partial(),
            ) from e
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line or line.startswith(b":"):
                continue
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload == b"[DONE]":
                return {
                    "content": "".join(content_parts),
                    "thinking": "".join(thinking_parts),
                    "finish_reason": finish_reason or "stop",
                    "usage": usage,
                }
            try:
                obj = json.loads(payload.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if isinstance(obj.get("usage"), dict):
                usage = obj["usage"]
            for choice in obj.get("choices") or []:
                fr = choice.get("finish_reason")
                if fr:
                    finish_reason = str(fr)
                delta = choice.get("delta") or {}
                msg = choice.get("message") or {}
                for key, parts, cb in (
                    ("content", content_parts, on_content),
                    ("reasoning_content", thinking_parts, on_thinking),
                    ("thinking", thinking_parts, on_thinking),
                ):
                    piece = delta.get(key) or msg.get(key)
                    if piece:
                        parts.append(str(piece))
                        if cb is not None:
                            cb(str(piece))
    return {
        "content": "".join(content_parts),
        "thinking": "".join(thinking_parts),
        "finish_reason": finish_reason or "incomplete_stream",
        "usage": usage,
    }


def _openai_chat(
    model: str,
    messages: list[dict[str, Any]],
    *,
    think: bool | str | None = None,
    on_thinking=None,
    on_content=None,
    options: dict[str, Any] | None = None,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    opts = options or {}
    max_tokens = int(opts.get("num_predict") or os.environ.get("BENCH_NUM_PREDICT", "2048"))
    temp = float(opts["temperature"]) if "temperature" in opts else 0.1
    oai_msgs: list[dict[str, str]] = []
    for m in messages:
        role = str(m.get("role") or "user")
        if role not in ("system", "user", "assistant"):
            role = "user"
        content = m.get("content")
        if not isinstance(content, str):
            content = json.dumps(content)
        oai_msgs.append({"role": role, "content": content})

    think_on = False
    if os.environ.get("BENCH_THINK", "0") not in ("0", "false", "off", ""):
        think_on = bool(think) if think is not None else True

    use_stream = _use_stream()
    # Clamp one call to remaining task budget (and a hard ceiling).
    ceiling = _http_ceiling_s()
    if timeout_s is None:
        call_budget = ceiling
    else:
        call_budget = max(1.0, min(float(timeout_s), ceiling))
    hard_deadline = time.perf_counter() + call_budget

    body: dict[str, Any] = {
        "model": model or MODEL,
        "messages": oai_msgs,
        "temperature": temp,
        "max_tokens": max_tokens,
        "stream": use_stream,
        "think": think_on,
    }

    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    content = ""
    thinking = ""
    finish_reason = "stop"
    usage: dict[str, Any] = {}
    try:
        first_byte = min(_first_byte_s(), call_budget)
        with urllib.request.urlopen(req, timeout=first_byte) as resp:
            if use_stream:
                try:
                    sock = resp.fp.raw._sock  # type: ignore[attr-defined]
                    remaining = max(1.0, hard_deadline - time.perf_counter())
                    sock.settimeout(min(_stall_s(), remaining))
                except Exception:
                    pass
                parsed = _read_sse_stream(
                    resp,
                    on_thinking=on_thinking,
                    on_content=on_content,
                    hard_deadline=hard_deadline,
                )
                content = parsed["content"]
                thinking = parsed["thinking"]
                finish_reason = parsed["finish_reason"] or "stop"
                usage = parsed.get("usage") or {}
            else:
                payload = json.loads(resp.read().decode())
                choice = (payload.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                content = msg.get("content") or ""
                thinking = msg.get("reasoning_content") or msg.get("thinking") or ""
                finish_reason = choice.get("finish_reason") or "stop"
                usage = payload.get("usage") or {}
                if on_thinking and thinking:
                    on_thinking(thinking)
                if on_content and content:
                    on_content(content)
    except StreamStallError as e:
        wall = time.perf_counter() - t0
        usage = e.usage
        eval_toks = int(usage.get("completion_tokens") or 0)
        return {
            "content": e.content,
            "thinking": e.thinking,
            "wall_s": wall,
            "load_s": 0.0,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "eval_tokens": eval_toks,
            "toks_per_s": 0.0,
            "done_reason": "stream_stall",
            "raw": None,
            "provider": "openai",
            "think": think_on,
            "stream": use_stream,
            "attempt": 1,
        }
    except (TimeoutError, socket.timeout):
        wall = time.perf_counter() - t0
        return {
            "content": "",
            "thinking": "",
            "wall_s": wall,
            "load_s": 0.0,
            "prompt_tokens": 0,
            "eval_tokens": 0,
            "toks_per_s": 0.0,
            "done_reason": "http_timeout",
            "raw": None,
            "provider": "openai",
            "think": think_on,
            "stream": use_stream,
            "attempt": 1,
        }
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"HTTP {e.code}: {err}") from e

    wall = time.perf_counter() - t0
    eval_toks = int(usage.get("completion_tokens") or 0)
    # OpenAI maps length truncation to finish_reason=length.
    done = finish_reason or "stop"
    if done in ("length", "max_tokens"):
        done = "length"
    return {
        "content": content,
        "thinking": thinking,
        "wall_s": wall,
        "load_s": 0.0,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "eval_tokens": eval_toks,
        "toks_per_s": (eval_toks / wall if wall > 0 else 0.0),
        "done_reason": done,
        "raw": None,
        "provider": "openai",
        "think": think_on,
        "stream": use_stream,
        "attempt": 1,
    }


def _patch_suite(mod: Any) -> None:
    def chat(
        model,
        messages,
        *,
        think=None,
        on_thinking=None,
        on_content=None,
        timeout_s=None,
    ):
        return _openai_chat(
            model,
            messages,
            think=think,
            on_thinking=on_thinking,
            on_content=on_content,
            options=getattr(mod, "OPTIONS", None),
            timeout_s=timeout_s,
        )

    mod.chat = chat


def _smoke() -> None:
    r = _openai_chat(MODEL, [{"role": "user", "content": "Reply with exactly: OK"}], timeout_s=60)
    preview = repr((r.get("content") or "")[:80])
    print(
        f"smoke ok wall={r['wall_s']:.1f}s stream={r.get('stream')} "
        f"done={r.get('done_reason')} content={preview}",
        flush=True,
    )


def run_suite(name: str) -> int:
    tag_prefix = os.environ.get("BENCH_TAG_PREFIX", "ds4_flash_q2imatrix")
    os.environ["BENCH_TAG"] = f"{tag_prefix}_{name}"
    modname = f"benches.{name}.bench"
    # Drop cached module so TAG/OPTIONS rebind from env.
    for key in list(sys.modules):
        if key == modname or key.startswith(f"benches.{name}."):
            del sys.modules[key]
    mod = importlib.import_module(modname)
    _patch_suite(mod)
    print(
        f"\n======== {name} model={mod.MODEL} tag={mod.TAG} base={BASE} "
        f"predict={os.environ.get('BENCH_NUM_PREDICT')} stream={_use_stream()} ========",
        flush=True,
    )
    return int(mod.main() or 0)


def main(argv: list[str]) -> int:
    suites = [a for a in argv[1:] if a in SUITES] or list(SUITES)
    print(
        f"suites={suites} model={MODEL} base={BASE} "
        f"predict={os.environ.get('BENCH_NUM_PREDICT')} "
        f"http_ceiling={_http_ceiling_s()}s stall={_stall_s()}s",
        flush=True,
    )
    _smoke()
    rc = 0
    for name in suites:
        try:
            code = run_suite(name)
            print(f"{name} finished rc={code}", flush=True)
            rc = rc or code
        except SystemExit as e:
            code = int(e.code) if isinstance(e.code, int) else 1
            print(f"{name} SystemExit {code}", flush=True)
            rc = rc or code
        except Exception as e:
            print(f"{name} FAILED: {type(e).__name__}: {e}", flush=True)
            rc = rc or 1
    print(f"ALL DONE rc={rc}", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
