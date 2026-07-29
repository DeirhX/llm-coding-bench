#!/usr/bin/env python3.14
"""Repohard: explore ledgerkit with tools, land a unified diff, grade via private pytest.

Usage:
  python run.py run repohard
  BENCH_SELFTEST=1 python -m benches.repohard
  BENCH_MODEL='qwen3-coder-next:q8_0' python -m benches.repohard
  BENCH_PROVIDER=cursor BENCH_MODEL='composer-2.5' python -m benches.repohard
  BENCH_TASKS='money_rounding_split,outbox_poison_retry' python -m benches.repohard
"""

from __future__ import annotations

import json
import os
import re
import shutil
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
from bench_lib.ollama_think import (
    sampler_options,  # noqa: E402
    RoundTranscript,
    default_num_predict,
    format_think_combined,
    parse_think,
    save_task_transcript,
    think_for_round,
    think_loop_nudge,
)
from bench_lib.paths import results_dir  # noqa: E402
from benches.repohard.tasks import (  # noqa: E402
    TASK_IDS,
    Task,
    build_tasks,
    gold_patch,
    grade_patch,
    prompt_for_provider,
    run_private_pytest,
)
from benches.repohard.tools import (  # noqa: E402
    ToolSession,
    extract_patch,
    fresh_fixture_copy,
)

OUT_DIR = results_dir("repohard")

SELFTEST = os.environ.get("BENCH_SELFTEST") == "1"
PROVIDER = os.environ.get("BENCH_PROVIDER", "ollama").strip().lower()
MODEL = "selftest" if SELFTEST else os.environ.get("BENCH_MODEL", "")
_TAG_BASE = re.sub(r"[^a-zA-Z0-9._-]", "_", MODEL or "model")
TAG = os.environ.get(
    "BENCH_TAG",
    "selftest_repohard"
    if SELFTEST
    else f"{'cursor_' if PROVIDER in ('cursor', 'cursor-cli', 'agent') else ''}{_TAG_BASE}_repohard",
)
THINK = parse_think()
OPTIONS = {
    **sampler_options(0.1),
    "num_ctx": int(os.environ.get("BENCH_NUM_CTX", "65536")),
    "num_predict": default_num_predict(8192, think_base=24576),
}

# gemma4 26B-A4B fabricates the harness half of the protocol: it emits <arch_tool>,
# then invents the <arch_result> block that the harness is supposed to send back,
# and repeats that dozens of times in one turn (103 fake results against 9 real
# tool calls on the worst task). parse_tool_call() searches for the first match and
# discards everything after it, so those tokens are already thrown away -- they buy
# nothing and cost up to 49k tokens and a task timeout. Stopping at <arch_result>
# leaves the preceding </arch_tool> intact, so the parser sees exactly what it saw
# before. Off by default so existing results stay comparable.
if os.environ.get("BENCH_STOP_FABRICATION", "0") == "1":
    OPTIONS["stop"] = ["<arch_result>"]

MAX_ROUNDS = int(os.environ.get("BENCH_MAX_ROUNDS", "40"))
MAX_TOOL_CALLS = int(os.environ.get("BENCH_MAX_TOOL_CALLS", "40"))
# When >0, after this many rounds inject a one-shot "emit arch_final now" nudge.
FINALIZE_AFTER = int(os.environ.get("BENCH_FINALIZE_AFTER", "0"))


def chat(
    model: str,
    messages: list[dict[str, str]],
    *,
    think: bool | str | None = None,
    on_thinking=None,
    on_content=None,
) -> dict[str, Any]:
    # Stream + keep_alive + stall retry; deltas flush into live transcripts.
    return ollama_chat(
        model,
        messages,
        options=OPTIONS,
        think=THINK if think is None else think,
        on_thinking=on_thinking,
        on_content=on_content,
    )


_TOOL_RE = re.compile(
    r"<(?:arch_tool|tool_call)>\s*(\{[\s\S]*?\})\s*</(?:arch_tool|tool_call)>",
    re.I,
)
_FINAL_RE = re.compile(
    r"<(?:arch_final|final_answer)>\s*([\s\S]*?)\s*</(?:arch_final|final_answer)>",
    re.I,
)
_FENCE_JSON = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.I)
_FENCE_DIFF = re.compile(r"```(?:diff|patch)\s*([\s\S]*?)\s*```", re.I)


def parse_tool_call(text: str) -> dict[str, Any] | None:
    m = _TOOL_RE.search(text)
    if not m:
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
    if blob:
        fence = _FENCE_JSON.search(blob)
        if fence:
            blob = fence.group(1).strip()
        else:
            blob = re.sub(r"^```(?:json)?\s*", "", blob.strip(), flags=re.I)
            blob = re.sub(r"\s*```$", "", blob.strip())
        try:
            obj = json.loads(blob)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            cleaned = re.sub(r",\s*}", "}", blob)
            cleaned = re.sub(r",\s*]", "]", cleaned)
            try:
                obj = json.loads(cleaned)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                # maybe the arch_final body is a raw diff
                if "--- " in blob and "+++ " in blob:
                    return {"patch": blob}
    # bare diff fence
    dm = _FENCE_DIFF.search(text)
    if dm:
        return {"patch": dm.group(1).strip()}
    # prose fallback — require --- and +++ at start of a line to avoid
    # false positives when the model discusses file paths in prose
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("--- a/") or stripped.startswith("+++ b/"):
            return {"patch": "\n".join(lines[i:]).strip()}
    return None


def run_agent_cursor(task: Task) -> dict[str, Any]:
    from bench_lib import cursor_cli

    # Per-task temp copy so ask-mode leakage cannot poison the canonical fixture.
    work = fresh_fixture_copy()
    try:
        prompt = prompt_for_provider(task.prompt, "cursor")
        from bench_lib.task_timeout import cursor_timeout_s

        resp = cursor_cli.chat(
            MODEL,
            prompt,
            mode=os.environ.get("BENCH_CURSOR_MODE", "ask"),
            workspace=work,
            timeout_s=cursor_timeout_s(),
        )
        content = resp.get("content") or ""
        thinking = resp.get("thinking") or ""
        final = parse_final_answer(content) or {}
        session = ToolSession(max_calls=MAX_TOOL_CALLS)
        assert task.grade is not None
        grade = task.grade(final, session)
        patch = extract_patch(final)
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
            "passed": grade.get("passed"),
            "total": grade.get("total"),
            "answer": {
                "patch": patch,
                "patch_bytes": grade.get("patch_bytes") or len(patch.encode("utf-8")),
                "patch_preview": grade.get("patch_preview") or patch[:1200],
                "apply_detail": grade.get("apply_detail"),
            },
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
            "raw_content": content[:8000],
        }
    finally:
        shutil.rmtree(work.parent, ignore_errors=True)


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
    finalize_nudged = False
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
            detail = resp.get("think_loop_detail") or abort_reason
            transcript.add_note(f"{abort_reason.upper()} aborted: {detail}")
        if resp.get("think_promoted"):
            transcript.add_note("THINK_PROMOTED: final scraped from thinking")
        messages.append({"role": "assistant", "content": content})

        final = parse_final_answer(content)
        if final is not None and parse_tool_call(content) is None:
            break

        if abort_reason in ("think_loop", "think_budget") and think_loop_nudges < 2:
            think_loop_nudges += 1
            nudge = think_loop_nudge(thinking=thinking, protocol="repohard")
            transcript.add_note(
                f"{abort_reason} nudge {think_loop_nudges}/2 "
                f"(tail {min(3000, len(thinking))} chars)"
            )
            messages.append({"role": "user", "content": nudge})
            continue

        call = parse_tool_call(content)
        if call is None:
            if round_i < MAX_ROUNDS - 1:
                nudge = (
                    "Protocol error: emit either one <arch_tool>{...}</arch_tool> "
                    'or a <arch_final>{"patch": "...unified diff..."}</arch_final>.'
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
                    "content": 'Tool budget exhausted. Provide <arch_final>{"patch":"..."} now.',
                }
            )
        elif (
            FINALIZE_AFTER > 0
            and not finalize_nudged
            and final is None
            and (round_i + 1) >= FINALIZE_AFTER
        ):
            finalize_nudged = True
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Round {round_i + 1}/{MAX_ROUNDS}: stop exploring. "
                        'Emit <arch_final>{"patch":"...unified diff..."}</arch_final> now '
                        "with your best minimal fix."
                    ),
                }
            )

    assert task.grade is not None
    grade = task.grade(final or {}, session)
    if timed_out:
        grade = {
            **grade,
            "ok": False,
            "score": 0,
            "detail": f"TIMEOUT: exceeded BENCH_TASK_TIMEOUT_S; partial {grade.get('detail')}",
            "passed": 0,
        }
    patch = extract_patch(final or {})
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
        "passed": grade.get("passed"),
        "total": grade.get("total"),
        "answer": {
            "patch": patch,
            "patch_bytes": grade.get("patch_bytes") or len(patch.encode("utf-8")),
            "patch_preview": (grade.get("patch_preview") or patch[:1200]),
            "apply_detail": grade.get("apply_detail"),
        },
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
        "raw_content": last_content[:8000],
    }


def run_agent(task: Task) -> dict[str, Any]:
    if PROVIDER in ("cursor", "cursor-cli", "agent"):
        return run_agent_cursor(task)
    if PROVIDER != "ollama":
        raise SystemExit(f"Unknown BENCH_PROVIDER={PROVIDER!r} (use ollama|cursor)")
    return run_agent_ollama(task)


def run_selftest() -> int:
    """Unpatched fixture fails each suite; gold patches make them pass; tools jail works."""
    session = ToolSession(max_calls=MAX_TOOL_CALLS)
    assert session.dispatch("read_file", {"path": "../private/gold/x.patch"})["ok"] is False
    assert session.dispatch("list_dir", {"path": "."})["ok"] is True
    assert session.dispatch("grep", {"pattern": "ledgerkit"})["ok"] is True

    fails: list[str] = []
    for tid in TASK_IDS:
        # unpatched must fail
        work = fresh_fixture_copy()
        try:
            r = run_private_pytest(work, tid)
            if r["ok"]:
                fails.append(f"{tid}: unpatched unexpectedly passed")
        finally:
            import shutil

            shutil.rmtree(work.parent, ignore_errors=True)

        # gold must pass via grader
        grade = grade_patch({"patch": gold_patch(tid)}, session, tid)
        if not grade.get("ok"):
            fails.append(f"{tid}: gold failed ({grade.get('detail')})")

        # empty patch must fail
        bad = grade_patch({"patch": ""}, session, tid)
        if bad.get("ok"):
            fails.append(f"{tid}: empty patch passed")

    if fails:
        print("SELFTEST FAILED:", fails, file=sys.stderr)
        return 1
    print("SELFTEST OK", json.dumps({"tasks": len(TASK_IDS)}))
    return 0


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
    if SELFTEST:
        return run_selftest()
    if not MODEL:
        raise SystemExit("Set BENCH_MODEL or BENCH_SELFTEST=1")
    tasks = select_tasks()

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

    try:
        if PROVIDER in ("cursor", "cursor-cli", "agent"):
            from bench_lib import cursor_cli
            from bench_lib.task_timeout import cursor_timeout_s

            warm = fresh_fixture_copy()
            try:
                cursor_cli.chat(
                    MODEL,
                    "Reply with the single word: pong",
                    mode="ask",
                    workspace=warm,
                    timeout_s=min(120.0, cursor_timeout_s()),
                )
            finally:
                shutil.rmtree(warm.parent, ignore_errors=True)
        else:
            chat(MODEL, [{"role": "user", "content": "Reply with the single word: pong"}])
    except Exception as e:  # noqa: BLE001
        print(f"warmup failed: {e}", file=sys.stderr)
        return 2

    done_ids = {str(r.get("task")) for r in results}
    with out_log.open("a", encoding="utf-8") as log:
        log.write(f"\n==== repohard provider={PROVIDER} {MODEL} tag={TAG} {stamp} ====\n")
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
                    "ok": False,
                    "score": 0,
                    "max_score": t.max_score,
                    "grade_detail": detail,
                    "done_reason": "task_timeout" if detail.startswith("TIMEOUT") else "error",
                }
            results = [x for x in results if x.get("task") != t.id]
            results.append(r)
            print(json.dumps({k: r[k] for k in r if k not in ("tool_trace", "pytest_output")}, indent=2))
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
