"""Ollama thinking / num_predict helpers shared by pyhard, arch, claim, repohard.

Thinking tokens share the same ``num_predict`` budget as answer tokens. Running
think-on at the default 16k often yields ``done_reason=length`` with empty
``content``. Prefer an explicit think level + a larger predict default.

Transcript dumps (``{tag}__{task_id}.txt``) keep full ``<think>`` traces on disk
without bloating the JSON result rows.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def format_think_combined(content: str, thinking: str) -> str:
    """Wrap thinking + answer the same way pyhard historically did."""
    content = content or ""
    thinking = thinking or ""
    if thinking.strip():
        return f"<think>\n{thinking}\n</think>\n{content}"
    return content


def save_task_transcript(out_dir: Path, tag: str, task_id: str, text: str) -> Path:
    """Write ``{tag}__{task_id}.txt`` under the bench results dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{tag}__{task_id}.txt"
    path.write_text(text or "", encoding="utf-8")
    return path


class RoundTranscript:
    """Accumulate per-round thinking + tool steps for agentic benches."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.thinking_chars = 0

    def add_round(
        self,
        round_i: int,
        *,
        thinking: str = "",
        content: str = "",
        done_reason: str | None = None,
        eval_tokens: int | None = None,
    ) -> None:
        thinking = thinking or ""
        content = content or ""
        self.thinking_chars += len(thinking)
        meta: list[str] = []
        if done_reason:
            meta.append(f"done_reason={done_reason}")
        if eval_tokens is not None:
            meta.append(f"eval_tokens={eval_tokens}")
        header = f"=== round {round_i} ==="
        if meta:
            header += " (" + ", ".join(meta) + ")"
        self.parts.append(header)
        self.parts.append(format_think_combined(content, thinking))

    def add_tool(self, name: str, args: dict[str, Any] | None, result_ok: Any) -> None:
        try:
            args_s = json.dumps(args or {}, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            args_s = repr(args)
        if len(args_s) > 2000:
            args_s = args_s[:2000] + "…"
        self.parts.append(f"=== tool {name} ok={result_ok} ===\n{args_s}")

    def add_note(self, text: str) -> None:
        self.parts.append(f"=== note ===\n{text}")

    def text(self) -> str:
        return "\n\n".join(p for p in self.parts if p is not None)

    def save(self, out_dir: Path, tag: str, task_id: str) -> Path:
        return save_task_transcript(out_dir, tag, task_id, self.text())


def parse_think(raw: str | None = None) -> bool | str:
    """Return a value for the top-level Ollama ``think`` field.

    Env ``BENCH_THINK``:
      - ``0`` / ``false`` / ``off`` → ``False``
      - ``1`` / ``true`` / ``on`` → ``True``, or ``BENCH_THINK_LEVEL`` if set
      - ``low`` / ``medium`` / ``high`` / ``max`` → that level string
    """
    v = (raw if raw is not None else os.environ.get("BENCH_THINK", "0")).strip().lower()
    if v in ("", "0", "false", "off", "no"):
        return False
    if v in ("1", "true", "on", "yes"):
        level = os.environ.get("BENCH_THINK_LEVEL", "").strip().lower()
        if level in ("low", "medium", "high", "max"):
            return level
        return True
    if v in ("low", "medium", "high", "max"):
        return v
    raise SystemExit(
        f"Invalid BENCH_THINK={v!r} (use 0|1|true|false|low|medium|high|max)"
    )


def thinking_enabled(think: bool | str | None = None) -> bool:
    t = parse_think() if think is None else think
    return t is not False


def default_num_predict(base: int, think_base: int | None = None) -> int:
    """Pick num_predict: explicit env wins; else raise default when thinking on."""
    if "BENCH_NUM_PREDICT" in os.environ:
        return int(os.environ["BENCH_NUM_PREDICT"])
    if thinking_enabled():
        return int(think_base if think_base is not None else max(base * 3, 49152))
    return base


def apply_think(body: dict[str, Any], think: bool | str | None = None) -> dict[str, Any]:
    """Set top-level ``think`` on an Ollama chat/generate body (never in options)."""
    t = parse_think() if think is None else think
    body["think"] = t
    return body


def apply_keep_alive(body: dict[str, Any], default: str = "-1") -> dict[str, Any]:
    """Keep the model loaded through long think generations.

    Default ``-1`` = indefinite. A finite keep-alive can expire mid-request and
    wedge the runner in ``Stopping...`` while the client waits forever
    (seen on qwen3.5 think @49k). Override with ``BENCH_KEEP_ALIVE``.
    """
    raw = os.environ.get("BENCH_KEEP_ALIVE", default)
    # Allow numeric -1 / seconds as well as duration strings like 60m.
    if isinstance(raw, str) and raw.lstrip("-").isdigit():
        body["keep_alive"] = int(raw)
    else:
        body["keep_alive"] = raw
    return body


def grade_from_response(content: str, thinking: str, *, scrape_thinking: bool = False) -> str:
    """Text to grade: prefer answer ``content``; do not mine truncated thinking."""
    content = content or ""
    thinking = thinking or ""
    if content.strip():
        return content
    if scrape_thinking and thinking.strip():
        return thinking
    # Empty content with a think trace usually means num_predict exhaustion.
    return content
