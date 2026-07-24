#!/usr/bin/env python3.14
"""Tools-first architecture / call-chain benchmark (Ollama or Cursor Agent CLI).

Usage:
  python run.py run arch
  BENCH_SELFTEST=1 python -m benches.arch
  BENCH_MODEL='qwen3-coder-next:q8_0' python -m benches.arch
  BENCH_PROVIDER=cursor BENCH_MODEL='composer-2.5' python -m benches.arch
  BENCH_TASKS='chain_delete_order,tenant_invoice_isolation' python -m benches.arch
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
_REPO = _ROOT.parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bench_lib.ollama_chat import chat as ollama_chat  # noqa: E402
from bench_lib.ollama_think import (  # noqa: E402
    RoundTranscript,
    default_num_predict,
    format_think_combined,
    parse_think,
    save_task_transcript,
    think_for_round,
    think_loop_nudge,
)
from bench_lib.paths import results_dir  # noqa: E402
from benches.arch.tasks import (  # noqa: E402
    SELFTEST_TRAJECTORIES,
    Task,
    build_tasks,
    prompt_for_provider,
)
from benches.shopapi.tools import FIXTURE_ROOT, ToolSession  # noqa: E402

OUT_DIR = results_dir("archbench")

SELFTEST = os.environ.get("BENCH_SELFTEST") == "1"
PROVIDER = os.environ.get("BENCH_PROVIDER", "ollama").strip().lower()
MODEL = "selftest" if SELFTEST else os.environ.get("BENCH_MODEL", "")
_TAG_BASE = re.sub(r"[^a-zA-Z0-9._-]", "_", MODEL or "model")
TAG = os.environ.get(
    "BENCH_TAG",
    "selftest_arch"
    if SELFTEST
    else f"{'cursor_' if PROVIDER in ('cursor', 'cursor-cli', 'agent') else ''}{_TAG_BASE}_arch",
)
FIXTURE = FIXTURE_ROOT

THINK = parse_think()
OPTIONS = {
    "temperature": float(os.environ.get("BENCH_TEMPERATURE", "0.1")),
    "num_ctx": int(os.environ.get("BENCH_NUM_CTX", "65536")),
    # Agent turns + thinking need headroom; default 24k when think-on.
    "num_predict": default_num_predict(8192, think_base=24576),
}

MAX_ROUNDS = int(os.environ.get("BENCH_MAX_ROUNDS", "32"))
MAX_TOOL_CALLS = int(os.environ.get("BENCH_MAX_TOOL_CALLS", "30"))
HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def chat(
    model: str,
    messages: list[dict[str, str]],
    *,
    think: bool | str | None = None,
    on_thinking=None,
    on_content=None,
) -> dict[str, Any]:
    # Retries / stall / keep_alive handled in ollama_chat (EOF still retried there).
    return ollama_chat(
        model,
        messages,
        options=OPTIONS,
        think=THINK if think is None else think,
        on_thinking=on_thinking,
        on_content=on_content,
    )


# Avoid <tool_call> — Ollama's Qwen3.5/3.6 parsers 500 with {"error":"EOF"} on that tag.
_TOOL_RE = re.compile(
    r"<(?:arch_tool|tool_call)>\s*(\{[\s\S]*?\})\s*</(?:arch_tool|tool_call)>",
    re.I,
)
_FINAL_RE = re.compile(
    r"<(?:arch_final|final_answer)>\s*([\s\S]*?)\s*</(?:arch_final|final_answer)>",
    re.I,
)
_FENCE_JSON = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.I)


def parse_tool_call(text: str) -> dict[str, Any] | None:
    m = _TOOL_RE.search(text)
    if not m:
        # fallback: bare JSON with name/arguments on its own
        m2 = re.search(
            r"\{\s*\"name\"\s*:\s*\"(list_dir|read_file|grep|find_refs)\"\s*,\s*\"arguments\"\s*:\s*\{[\s\S]*?\}\s*\}",
            text,
        )
        if not m2:
            return None
        try:
            return json.loads(m2.group(0))
        except json.JSONDecodeError:
            return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def parse_final_answer(text: str) -> dict[str, Any] | None:
    m = _FINAL_RE.search(text)
    blob = None
    if m:
        blob = m.group(1).strip()
    else:
        fences = _FENCE_JSON.findall(text)
        if fences:
            blob = fences[-1].strip()
    if not blob:
        # last resort: outermost JSON object in content
        m3 = re.search(r"\{[\s\S]*\}", text)
        if m3 and ("chain" in m3.group(0) or "citations" in m3.group(0) or "root_cause" in m3.group(0) or "touch_files" in m3.group(0) or "violated" in m3.group(0) or "findings" in m3.group(0) or "enforced_at" in m3.group(0) or "i4_holds" in m3.group(0)):
            blob = m3.group(0)
    if not blob:
        return None
    # Cursor (and some Ollama models) nest a ```json fence inside <arch_final>
    fence = _FENCE_JSON.search(blob)
    if fence:
        blob = fence.group(1).strip()
    else:
        blob = re.sub(r"^```(?:json)?\s*", "", blob.strip(), flags=re.I)
        blob = re.sub(r"\s*```$", "", blob.strip())
    try:
        obj = json.loads(blob)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        # try trim trailing commas
        cleaned = re.sub(r",\s*}", "}", blob)
        cleaned = re.sub(r",\s*]", "]", cleaned)
        try:
            obj = json.loads(cleaned)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None


def run_agent_cursor(task: Task) -> dict[str, Any]:
    """Single-shot Cursor Agent ask-mode over the shopapi workspace."""
    from bench_lib import cursor_cli
    from bench_lib.task_timeout import cursor_timeout_s

    prompt = prompt_for_provider(task.prompt, "cursor")
    resp = cursor_cli.chat(
        MODEL,
        prompt,
        mode=os.environ.get("BENCH_CURSOR_MODE", "ask"),
        workspace=FIXTURE,
        timeout_s=cursor_timeout_s(),
    )
    content = resp.get("content") or ""
    thinking = resp.get("thinking") or ""
    final = parse_final_answer(content) or {}
    # Do NOT invent files_read from citations — evidence requires real tool reads.
    # Cursor ask-mode does not expose a tool trace here, so evidence points stay 0.
    session = ToolSession(max_calls=MAX_TOOL_CALLS)
    grade = task.grade(final, session)
    transcript_path = save_task_transcript(
        OUT_DIR, TAG, task.id, format_think_combined(content, thinking)
    )
    return {
        "model": MODEL,
        "provider": "cursor",
        "task": task.id,
        "title": task.title,
        "family": task.family,
        "ok": bool(grade.get("ok")),
        "score": int(grade.get("score") or 0),
        "max_score": int(grade.get("max_score") or task.max_score),
        "grade_detail": grade.get("detail"),
        "answer": final,
        "tool_calls": None,
        "files_read": sorted(session.files_read),
        "tool_trace": [],
        "wall_s": round(float(resp.get("wall_s") or 0), 2),
        "prompt_tokens": int(resp.get("prompt_tokens") or 0),
        "eval_tokens": int(resp.get("eval_tokens") or 0),
        "rounds": 1,
        "done_reason": resp.get("done_reason"),
        "num_ctx": OPTIONS["num_ctx"],
        "num_predict": OPTIONS["num_predict"],
        "last_content_chars": len(content),
        "thinking_chars": len(thinking),
        "transcript": str(transcript_path),
        "session_id": resp.get("session_id"),
        "raw_content": content,
    }


def run_agent_ollama(task: Task) -> dict[str, Any]:
    from bench_lib.task_timeout import task_timeout_s

    session = ToolSession(max_calls=MAX_TOOL_CALLS)
    messages = [{"role": "user", "content": task.prompt}]
    tool_trace: list[dict[str, Any]] = []
    transcript = RoundTranscript(OUT_DIR, TAG, task.id)
    totals = {
        "wall_s": 0.0,
        "prompt_tokens": 0,
        "eval_tokens": 0,
        "rounds": 0,
        "done_reason": None,
    }
    final: dict[str, Any] | None = None
    last_content = ""
    deadline = time.perf_counter() + task_timeout_s()
    timed_out = False
    think_loop_nudges = 0

    for round_i in range(MAX_ROUNDS):
        if time.perf_counter() >= deadline:
            timed_out = True
            totals["done_reason"] = "task_timeout"
            transcript.add_note("TIMEOUT: exceeded BENCH_TASK_TIMEOUT_S")
            break
        totals["rounds"] = round_i + 1
        transcript.begin_round(round_i + 1)
        round_think = think_for_round(round_i, THINK)
        resp = chat(
            MODEL,
            messages,
            think=round_think,
            on_thinking=transcript.on_thinking_delta,
            on_content=transcript.on_content_delta,
        )
        totals["wall_s"] += resp["wall_s"]
        totals["prompt_tokens"] += resp["prompt_tokens"]
        totals["eval_tokens"] += resp["eval_tokens"]
        totals["done_reason"] = resp["done_reason"]
        # Promote only a closed <arch_final> from think (see maybe_promote_response);
        # never substitute the raw think trace for empty content.
        content = resp["content"] or ""
        thinking = resp.get("thinking") or ""
        last_content = content
        transcript.end_round(
            thinking=thinking,
            content=content,
            done_reason=resp.get("done_reason"),
            eval_tokens=resp.get("eval_tokens"),
        )
        abort_reason = resp.get("done_reason") or ""
        if abort_reason in ("think_loop", "think_budget"):
            transcript.add_note(
                f"{abort_reason.upper()} aborted: "
                f"{resp.get('think_loop_detail') or abort_reason}"
            )
        if resp.get("think_promoted"):
            transcript.add_note("THINK_PROMOTED: final scraped from thinking")
        messages.append({"role": "assistant", "content": content})

        final = parse_final_answer(content)
        if final is not None and parse_tool_call(content) is None:
            break

        if abort_reason in ("think_loop", "think_budget") and think_loop_nudges < 2:
            think_loop_nudges += 1
            nudge = think_loop_nudge(thinking=thinking, protocol="repohard")
            transcript.add_note(f"{abort_reason} nudge {think_loop_nudges}/2")
            messages.append({"role": "user", "content": nudge})
            continue

        call = parse_tool_call(content)
        if call is None:
            # nudge once if model forgot protocol
            if round_i < MAX_ROUNDS - 1:
                nudge = (
                    "Protocol error: emit either one <arch_tool>{...}</arch_tool> "
                    "or a <arch_final>{...}</arch_final> JSON object. No other chatter."
                )
                transcript.add_note(nudge)
                messages.append({"role": "user", "content": nudge})
                continue
            break

        name = str(call.get("name") or "")
        args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        result = session.dispatch(name, args)
        tool_trace.append({"name": name, "arguments": args, "result_ok": result.get("ok")})
        transcript.add_tool(name, args, result.get("ok"))
        messages.append(
            {
                "role": "user",
                "content": "<arch_result>\n"
                + json.dumps(result, indent=2)[:12000]
                + "\n</arch_result>\nContinue with another arch_tool or arch_final.",
            }
        )
        if session.remaining() <= 0 and final is None:
            messages.append(
                {
                    "role": "user",
                    "content": "Tool budget exhausted. Provide <arch_final> JSON now.",
                }
            )

    grade = task.grade(final or {}, session)
    if timed_out:
        grade = {
            **grade,
            "ok": False,
            "score": 0,
            "detail": f"TIMEOUT: exceeded BENCH_TASK_TIMEOUT_S; partial {grade.get('detail')}",
        }
    transcript_path = transcript.save()
    return {
        "model": MODEL,
        "provider": "ollama",
        "task": task.id,
        "title": task.title,
        "family": task.family,
        "ok": bool(grade.get("ok")),
        "score": int(grade.get("score") or 0),
        "max_score": int(grade.get("max_score") or task.max_score),
        "grade_detail": grade.get("detail"),
        "answer": final,
        "tool_calls": len(session.calls),
        "files_read": sorted(session.files_read),
        "tool_trace": tool_trace,
        "wall_s": round(totals["wall_s"], 2),
        "prompt_tokens": totals["prompt_tokens"],
        "eval_tokens": totals["eval_tokens"],
        "rounds": totals["rounds"],
        "done_reason": totals["done_reason"],
        "num_ctx": OPTIONS["num_ctx"],
        "num_predict": OPTIONS["num_predict"],
        "last_content_chars": len(last_content),
        "thinking_chars": transcript.thinking_chars,
        "transcript": str(transcript_path),
        "raw_content": last_content,
    }


def run_agent(task: Task) -> dict[str, Any]:
    if PROVIDER in ("cursor", "cursor-cli", "agent"):
        return run_agent_cursor(task)
    if PROVIDER != "ollama":
        raise SystemExit(f"Unknown BENCH_PROVIDER={PROVIDER!r} (use ollama|cursor)")
    return run_agent_ollama(task)


def run_agent_selftest(task: Task) -> dict[str, Any]:
    session = ToolSession(max_calls=MAX_TOOL_CALLS)
    traj = SELFTEST_TRAJECTORIES.get(task.id)
    if not traj:
        # generic: read required files then empty answer (should score low except we skip)
        for f in task.required_files:
            session.dispatch("read_file", {"path": f})
        answer: dict[str, Any] = {}
    else:
        for name, args in traj["tools"]:
            session.dispatch(name, args)
        answer = dict(traj["answer"])
    grade = task.grade(answer, session)
    return {
        "model": "selftest",
        "task": task.id,
        "title": task.title,
        "family": task.family,
        "ok": bool(grade.get("ok")),
        "score": int(grade.get("score") or 0),
        "max_score": int(grade.get("max_score") or task.max_score),
        "grade_detail": grade.get("detail"),
        "answer": answer,
        "tool_calls": len(session.calls),
        "files_read": sorted(session.files_read),
        "wall_s": 0.0,
        "prompt_tokens": 0,
        "eval_tokens": 0,
        "rounds": len(session.calls),
        "done_reason": "selftest",
        "num_ctx": OPTIONS["num_ctx"],
        "num_predict": OPTIONS["num_predict"],
    }


def select_tasks() -> list[Task]:
    all_tasks = build_tasks()
    filt = os.environ.get("BENCH_TASKS", "").strip()
    if not filt:
        return all_tasks
    want = {x.strip() for x in filt.split(",") if x.strip()}
    chosen = [t for t in all_tasks if t.id in want]
    if not chosen:
        raise SystemExit(f"No tasks matched BENCH_TASKS={filt!r}")
    return chosen


def main() -> int:
    if not SELFTEST and not MODEL:
        raise SystemExit("Set BENCH_MODEL or BENCH_SELFTEST=1")
    tasks = select_tasks()
    if SELFTEST:
        # Grade gold trajectories for tasks that have them; for others require score machinery doesn't crash
        results = []
        for t in tasks:
            if t.id in SELFTEST_TRAJECTORIES:
                r = run_agent_selftest(t)
            else:
                # smoke: tools work + grader accepts empty poorly
                session = ToolSession()
                session.dispatch("list_dir", {"path": "."})
                g = t.grade({}, session)
                r = {
                    "model": "selftest",
                    "task": t.id,
                    "ok": True,  # smoke only
                    "score": int(g.get("score") or 0),
                    "max_score": t.max_score,
                    "grade_detail": f"smoke empty→{g.get('detail')}",
                    "smoke": True,
                }
            results.append(r)
            print(json.dumps(r, indent=2))
        # Require gold trajectories to pass
        fails = [
            r
            for r in results
            if r.get("task") in SELFTEST_TRAJECTORIES and (r["score"] < 8 or not r.get("ok"))
        ]
        if fails:
            print("SELFTEST FAILED:", [f["task"] for f in fails], file=sys.stderr)
            return 1
        # tool unit checks
        s = ToolSession()
        assert s.dispatch("read_file", {"path": "../etc/passwd"})["ok"] is False
        assert s.dispatch("grep", {"pattern": "cancel_order"})["ok"] is True
        print("SELFTEST OK")
        return 0

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_json = OUT_DIR / f"{TAG}_{stamp}.json"
    out_log = OUT_DIR / f"{TAG}.log"
    latest_path = OUT_DIR / f"{TAG}_latest.json"
    merge_latest = os.environ.get("BENCH_MERGE_LATEST", "0") == "1"
    results: list[dict[str, Any]] = []
    if merge_latest and latest_path.is_file():
        try:
            prev = json.loads(latest_path.read_text(encoding="utf-8"))
            if isinstance(prev, list):
                results = [r for r in prev if isinstance(r, dict) and r.get("task")]
        except (OSError, json.JSONDecodeError):
            results = []

    # warmup
    try:
        if PROVIDER in ("cursor", "cursor-cli", "agent"):
            from bench_lib import cursor_cli
            from bench_lib.task_timeout import cursor_timeout_s

            cursor_cli.chat(
                MODEL,
                "Reply with the single word: pong",
                mode="ask",
                workspace=FIXTURE,
                timeout_s=min(120.0, cursor_timeout_s()),
            )
        else:
            chat(MODEL, [{"role": "user", "content": "Reply with the single word: pong"}])
    except Exception as e:  # noqa: BLE001
        print(f"warmup failed: {e}", file=sys.stderr)
        return 2

    done_ids = {str(r.get("task")) for r in results}
    with out_log.open("a", encoding="utf-8") as log:
        log.write(f"\n==== archbench provider={PROVIDER} {MODEL} tag={TAG} {stamp} ====\n")
        for t in tasks:
            if merge_latest and t.id in done_ids:
                print(f"-- {t.id} ... skip (merged)", flush=True)
                continue
            print(f"-- {t.id} ...", flush=True)
            log.write(f"-- {t.id} ...\n")
            try:
                r = run_agent(t)
            except Exception as e:  # noqa: BLE001
                name = type(e).__name__
                detail = f"ERROR: {name}: {e}"
                if name == "TimeoutExpired" or "timed out" in str(e).lower():
                    detail = f"TIMEOUT: exceeded BENCH_TASK_TIMEOUT_S / Cursor timeout ({e})"
                r = {
                    "model": MODEL,
                    "provider": PROVIDER,
                    "task": t.id,
                    "title": t.title,
                    "family": t.family,
                    "ok": False,
                    "score": 0,
                    "max_score": t.max_score,
                    "grade_detail": detail,
                    "done_reason": "task_timeout" if detail.startswith("TIMEOUT") else "error",
                }
            # replace prior row for this task id when merging
            results = [x for x in results if x.get("task") != t.id]
            results.append(r)
            print(json.dumps({k: r[k] for k in r if k not in ("tool_trace", "answer")}, indent=2))
            log.write(json.dumps(r, indent=2) + "\n")
            out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
            latest_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    total = sum(r.get("score", 0) for r in results)
    mx = sum(r.get("max_score", 0) for r in results)
    passed = sum(1 for r in results if r.get("ok"))
    summary = {
        "model": MODEL,
        "tag": TAG,
        "score": total,
        "max_score": mx,
        "pass": passed,
        "tasks": len(results),
        "path": str(out_json),
    }
    print("SUMMARY", json.dumps(summary))
    (OUT_DIR / f"{TAG}_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
