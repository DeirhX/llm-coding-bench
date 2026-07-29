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
import re
from pathlib import Path
from typing import Any


class ThinkLoopError(RuntimeError):
    """Thinking stream aborted (loop / budget); recover or promote a final."""

    def __init__(
        self,
        message: str,
        *,
        thinking: str = "",
        content: str = "",
        detail: str = "",
        reason: str = "think_loop",
    ) -> None:
        super().__init__(message)
        self.thinking = thinking or ""
        self.content = content or ""
        self.detail = detail or message
        self.reason = reason or "think_loop"


def _env_flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    )


def sampler_options(default_temperature: float = 0.1) -> dict[str, Any]:
    """Return the temperature to send, or nothing at all.

    ``BENCH_TEMPERATURE=auto`` omits the key entirely so Ollama falls back to the
    model's own Modelfile value. That distinction is not academic: gemma4:31b ships
    ``temperature 1`` and gemma4:26b ships no sampler at all, inheriting Ollama's
    0.8, while every score in this repo was measured at 0.1. An interactive client
    that sends no sampler therefore gets a model nobody here has benchmarked.
    """
    raw = os.environ.get("BENCH_TEMPERATURE", "").strip().lower()
    if raw in ("auto", "model", "modelfile", "default"):
        return {}
    return {"temperature": float(raw) if raw else default_temperature}


def realism_mode() -> bool:
    """Disable every harness rescue at once, because no client provides them.

    An editor or agent CLI gives the model one chance per request. Nothing watches
    the stream for degenerate repetition and aborts it, nothing re-prompts after a
    failed turn, and nothing salvages a final answer that was left behind in the
    thinking channel. With ``BENCH_REALISM=1`` the bench behaves the same way, so a
    score answers "could I code with this" instead of "could my harness rescue it".
    """
    return _env_flag("BENCH_REALISM", "0")


def think_loop_enabled() -> bool:
    if realism_mode():
        return False
    return _env_flag("BENCH_THINK_LOOP", "1")


def think_promote_enabled() -> bool:
    """When think is aborted or empty-content, promote a complete final from think."""
    if realism_mode():
        return False
    return _env_flag("BENCH_THINK_PROMOTE", "1")


def think_max_chars() -> int:
    """Hard cap on thinking characters per completion (0 = unlimited).

    Shared ``num_predict`` cannot stop rumination early; this client-side cap
    aborts the stream so the agent can emit (or we can promote a drafted final).
    """
    if realism_mode():
        return 0
    raw = os.environ.get("BENCH_THINK_MAX_CHARS", "0").strip()
    if not raw:
        return 0
    return max(0, int(raw))


_PROMOTE_FINAL_RE = re.compile(
    r"<(?:arch_final|final_answer)>\s*([\s\S]*?)\s*</(?:arch_final|final_answer)>",
    re.I,
)
_PROMOTE_DIFF_RE = re.compile(r"```(?:diff|patch)\s*([\s\S]*?)\s*```", re.I)


def promote_final_from_thinking(thinking: str) -> str | None:
    """If thinking already contains a complete graded final, return it as content.

    Prevents the common failure mode: fix finished inside ``<think>``, never emitted.
    Only promotes closed tags / fences — not half-drafted JSON.
    """
    if not think_promote_enabled():
        return None
    text = thinking or ""
    if not text.strip():
        return None
    m = _PROMOTE_FINAL_RE.search(text)
    if m:
        body = m.group(1).strip()
        fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", body, re.I)
        if fence:
            body = fence.group(1).strip()
        else:
            body = re.sub(r"^```(?:json)?\s*", "", body, flags=re.I)
            body = re.sub(r"\s*```$", "", body.strip())
        if body.startswith("{"):
            try:
                obj = json.loads(body)
            except json.JSONDecodeError:
                cleaned = re.sub(r",\s*}", "}", body)
                cleaned = re.sub(r",\s*]", "]", cleaned)
                try:
                    obj = json.loads(cleaned)
                except json.JSONDecodeError:
                    obj = None
            if isinstance(obj, dict) and (
                "patch" in obj
                or "answers" in obj
                or any(str(k).startswith("c") and str(k)[1:].isdigit() for k in obj)
            ):
                return (
                    "<arch_final>\n"
                    + json.dumps(obj, ensure_ascii=False)
                    + "\n</arch_final>"
                )
        if "--- " in body and "+++ " in body:
            return f"<arch_final>\n{json.dumps({'patch': body}, ensure_ascii=False)}\n</arch_final>"
    dm = _PROMOTE_DIFF_RE.search(text)
    if dm and "--- " in dm.group(1) and "+++ " in dm.group(1):
        patch = dm.group(1).strip()
        return (
            "<arch_final>\n"
            + json.dumps({"patch": patch}, ensure_ascii=False)
            + "\n</arch_final>"
        )
    return None


def maybe_promote_response(
    content: str,
    thinking: str,
    *,
    done_reason: str | None = None,
) -> tuple[str, str | None, bool]:
    """Return (content, done_reason, promoted). Promotes only when content empty."""
    content = content or ""
    if content.strip():
        return content, done_reason, False
    promoted = promote_final_from_thinking(thinking or "")
    if not promoted:
        return content, done_reason, False
    return promoted, "think_promoted", True


class ThinkLoopDetector:
    """Detect repetitive thinking — short mantras *and* long multi-line cycles.

    Triggers (any one):
      1. Same non-empty line repeated consecutively ``line_repeat`` times
      2. Exact cycle of ``bl`` lines repeating ``block_repeat`` times (multi-scale)
      3. Last ``char_window`` chars already appeared ``char_repeat`` times in recent text
      4. Signature phrase spam in the recent line window
    """

    _SIG_PHRASES = (
        "i will emit the final answer",
        "i will output the patch",
        "i'm done.",
        "i am done.",
        "one last check:",
        "one detail:",
        "this is correct.",
        "this matches the file content",
    )

    def __init__(
        self,
        *,
        line_repeat: int | None = None,
        block_lines: int | None = None,
        block_repeat: int | None = None,
        char_window: int | None = None,
        char_repeat: int | None = None,
        phrase_repeat: int | None = None,
    ) -> None:
        self.line_repeat = int(
            os.environ.get("BENCH_THINK_LOOP_LINE_REPEAT", line_repeat or 8)
        )
        # Legacy single size still honored; multi-scale covers long cycles.
        legacy_bl = int(
            os.environ.get("BENCH_THINK_LOOP_BLOCK_LINES", block_lines or 4)
        )
        raw_sizes = os.environ.get(
            "BENCH_THINK_LOOP_BLOCK_SIZES",
            f"{legacy_bl},8,12,16,24,32,48,64",
        )
        self.block_sizes = sorted(
            {
                int(x)
                for x in raw_sizes.split(",")
                if x.strip().isdigit() and int(x) > 0
            }
        )
        self.block_repeat = int(
            os.environ.get("BENCH_THINK_LOOP_BLOCK_REPEAT", block_repeat or 3)
        )
        self.char_window = int(
            os.environ.get("BENCH_THINK_LOOP_CHAR_WINDOW", char_window or 500)
        )
        self.char_repeat = int(
            os.environ.get("BENCH_THINK_LOOP_CHAR_REPEAT", char_repeat or 3)
        )
        self.phrase_repeat = int(
            os.environ.get("BENCH_THINK_LOOP_PHRASE_REPEAT", phrase_repeat or 10)
        )
        self._buf = ""
        self._last_line: str | None = None
        self._consec = 0
        self._lines: list[str] = []
        self._block_hits: dict[int, int] = {bl: 0 for bl in self.block_sizes}
        self._text = ""
        self._max_lines = 4000
        self._max_text = 20000

    def feed(self, chunk: str) -> None:
        if not chunk or not think_loop_enabled():
            return
        self._text = (self._text + chunk)[-self._max_text :]
        self._check_char_cycle()
        self._buf += chunk
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._on_line(line.strip())

    def _check_char_cycle(self) -> None:
        w = self.char_window
        if w < 80 or len(self._text) < w * (self.char_repeat + 1):
            return
        needle = self._text[-w:]
        # Ignore low-entropy needles (spaces / single char spam).
        if len(set(needle)) < 12:
            return
        prior = self._text[:-w]
        # Count non-overlapping occurrences in the prior window.
        count = 0
        start = 0
        while True:
            i = prior.find(needle, start)
            if i < 0:
                break
            count += 1
            start = i + w
            if count >= self.char_repeat:
                raise ThinkLoopError(
                    f"think loop: {w}-char window repeated {count + 1}x",
                    detail=f"char_cycle {w}c ×{count + 1}: {needle[:100]!r}…",
                )

    def _on_line(self, line: str) -> None:
        if not line:
            return
        if line == self._last_line:
            self._consec += 1
            if self._consec >= self.line_repeat:
                raise ThinkLoopError(
                    f"think loop: line repeated {self._consec}x",
                    detail=f"line_repeat {self._consec}x: {line[:120]!r}",
                )
        else:
            self._last_line = line
            self._consec = 1
        self._lines.append(line)
        if len(self._lines) > self._max_lines:
            self._lines = self._lines[-self._max_lines :]
        self._check_block_cycles()
        self._check_phrase_spam()

    def _check_block_cycles(self) -> None:
        n = len(self._lines)
        for bl in self.block_sizes:
            if n < bl * 2:
                continue
            a = self._lines[-bl:]
            b = self._lines[-2 * bl : -bl]
            if a == b:
                self._block_hits[bl] = self._block_hits.get(bl, 0) + 1
                if self._block_hits[bl] >= self.block_repeat:
                    sample = " | ".join(a[:4])[:160]
                    raise ThinkLoopError(
                        f"think loop: {bl}-line block repeated {self._block_hits[bl]}x",
                        detail=f"block_cycle {bl}×{self._block_hits[bl]}: {sample!r}",
                    )
            else:
                self._block_hits[bl] = 0

    def _check_phrase_spam(self) -> None:
        window = self._lines[-120:]
        if len(window) < 40:
            return
        blob = "\n".join(window).lower()
        for phrase in self._SIG_PHRASES:
            n = blob.count(phrase)
            if n >= self.phrase_repeat:
                raise ThinkLoopError(
                    f"think loop: phrase {phrase!r} ×{n} in recent lines",
                    detail=f"phrase_spam {n}x: {phrase!r}",
                )


def think_loop_nudge(*, thinking: str, protocol: str = "repohard") -> str:
    """User message after a think-loop abort — include thinking tail so work survives."""
    tail = (thinking or "").strip()[-3000:]
    if protocol == "pyhard":
        body = (
            "STOP. Your hidden thinking was aborted because it was stuck repeating. "
            "Using the analysis below, output the final Python solution NOW "
            "(complete code only, no more rumination).\n\n"
        )
    elif protocol == "claim":
        body = (
            "STOP. Your thinking was aborted (repetition loop). "
            "Emit <arch_final> with all c01..c20 booleans NOW based on the analysis below. "
            "No more tools unless one critical read is missing.\n\n"
        )
    else:
        body = (
            "STOP. Your thinking was aborted because it was stuck in a repetition loop. "
            "Using the truncated analysis below, emit "
            '<arch_final>{"patch":"...unified diff..."}</arch_final> NOW '
            "with the fix you already decided. "
            "Do not re-analyze. Tools only if one file read is still required.\n\n"
        )
    if tail:
        body += "Truncated thinking (tail):\n```\n" + tail + "\n```\n"
    return body


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
    """Live + final per-round thinking dump for agentic benches.

    Opens ``{tag}__{task_id}.txt`` immediately and flushes think/content deltas
    as the Ollama stream arrives so ``tail -f`` works mid-task.
    """

    def __init__(self, out_dir: Path | None = None, tag: str = "", task_id: str = "") -> None:
        self.parts: list[str] = []
        self.thinking_chars = 0
        self.path: Path | None = None
        self._fh: Any = None
        self._round_open = False
        self._in_think = False
        self._think_opened = False
        self._content_started = False
        if out_dir is not None and tag and task_id:
            self.open(out_dir, tag, task_id)

    def open(self, out_dir: Path, tag: str, task_id: str) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        self.path = out_dir / f"{tag}__{task_id}.txt"
        self._fh = self.path.open("w", encoding="utf-8")
        self._write(f"# transcript tag={tag} task={task_id}\n")
        return self.path

    def _write(self, s: str) -> None:
        if self._fh is None:
            return
        self._fh.write(s)
        self._fh.flush()

    def begin_round(
        self,
        round_i: int,
        *,
        done_reason: str | None = None,
        eval_tokens: int | None = None,
    ) -> None:
        self._close_round_tags()
        meta: list[str] = []
        if done_reason:
            meta.append(f"done_reason={done_reason}")
        if eval_tokens is not None:
            meta.append(f"eval_tokens={eval_tokens}")
        header = f"\n=== round {round_i} ==="
        if meta:
            header += " (" + ", ".join(meta) + ")"
        self._write(header + "\n")
        self._round_open = True
        self._in_think = False
        self._think_opened = False
        self._content_started = False

    def on_thinking_delta(self, chunk: str) -> None:
        if not chunk:
            return
        self.thinking_chars += len(chunk)
        if not self._think_opened:
            self._write("<think>\n")
            self._think_opened = True
            self._in_think = True
        self._write(chunk)

    def on_content_delta(self, chunk: str) -> None:
        if not chunk:
            return
        if self._in_think:
            self._write("\n</think>\n")
            self._in_think = False
        if not self._content_started:
            self._content_started = True
        self._write(chunk)

    def end_round(
        self,
        *,
        thinking: str = "",
        content: str = "",
        done_reason: str | None = None,
        eval_tokens: int | None = None,
    ) -> None:
        """Finish a round; if no live deltas were written, dump the full blob."""
        thinking = thinking or ""
        content = content or ""
        if not self._think_opened and not self._content_started:
            # Non-streaming / empty-delta fallback
            if not self._round_open:
                self.begin_round(0, done_reason=done_reason, eval_tokens=eval_tokens)
            self._write(format_think_combined(content, thinking))
            if thinking:
                self.thinking_chars += len(thinking)
        else:
            if thinking and not self._think_opened:
                self.on_thinking_delta(thinking)
            if content and not self._content_started:
                self.on_content_delta(content)
        self._close_round_tags()
        if done_reason or eval_tokens is not None:
            bits = []
            if done_reason:
                bits.append(f"done_reason={done_reason}")
            if eval_tokens is not None:
                bits.append(f"eval_tokens={eval_tokens}")
            self._write("# " + ", ".join(bits) + "\n")
        # keep in-memory copy for callers that still use .text()
        self.parts.append(format_think_combined(content, thinking))

    def add_round(
        self,
        round_i: int,
        *,
        thinking: str = "",
        content: str = "",
        done_reason: str | None = None,
        eval_tokens: int | None = None,
    ) -> None:
        """Compat: write a whole round at once (non-streaming)."""
        self.begin_round(round_i, done_reason=done_reason, eval_tokens=eval_tokens)
        self.end_round(
            thinking=thinking,
            content=content,
            done_reason=done_reason,
            eval_tokens=eval_tokens,
        )

    def _close_round_tags(self) -> None:
        if self._in_think:
            self._write("\n</think>\n")
            self._in_think = False
        self._round_open = False

    def add_tool(self, name: str, args: dict[str, Any] | None, result_ok: Any) -> None:
        self._close_round_tags()
        try:
            args_s = json.dumps(args or {}, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            args_s = repr(args)
        if len(args_s) > 2000:
            args_s = args_s[:2000] + "…"
        block = f"=== tool {name} ok={result_ok} ===\n{args_s}"
        self.parts.append(block)
        self._write("\n" + block + "\n")

    def add_note(self, text: str) -> None:
        self._close_round_tags()
        block = f"=== note ===\n{text}"
        self.parts.append(block)
        self._write("\n" + block + "\n")

    def text(self) -> str:
        if self.path and self.path.exists():
            return self.path.read_text(encoding="utf-8")
        return "\n\n".join(p for p in self.parts if p is not None)

    def save(self, out_dir: Path | None = None, tag: str = "", task_id: str = "") -> Path:
        self._close_round_tags()
        if self._fh is not None:
            self._fh.flush()
            self._fh.close()
            self._fh = None
            assert self.path is not None
            return self.path
        if out_dir is None or not tag or not task_id:
            raise ValueError("save() needs an open transcript or out_dir/tag/task_id")
        return save_task_transcript(out_dir, tag, task_id, self.text())

    def close(self) -> None:
        if self._fh is not None:
            self._close_round_tags()
            self._fh.flush()
            self._fh.close()
            self._fh = None


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


def think_for_round(round_i: int, think: bool | str | None = None) -> bool | str:
    """Think setting for agent round ``round_i`` (0-based).

    ``BENCH_THINK_ROUNDS``:
      - unset / ``all`` / ``-1`` → every round uses ``think``
      - ``N`` → only the first N rounds think; later rounds force ``False``
    """
    base = parse_think() if think is None else think
    raw = os.environ.get("BENCH_THINK_ROUNDS", "").strip().lower()
    if raw in ("", "all", "-1"):
        return base
    n = int(raw)
    if n <= 0:
        return False
    return base if round_i < n else False


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
