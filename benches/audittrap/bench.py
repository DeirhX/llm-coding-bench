#!/usr/bin/env python3.14
"""Audittrap: claim battery + fix/wontfix over synthetic miniharness.

Usage:
  python run.py run audittrap
  BENCH_SELFTEST=1 python -m benches.audittrap
  BENCH_MODEL='qwen3-coder-next:q8_0' python -m benches.audittrap
  BENCH_PROVIDER=cursor BENCH_MODEL='claude-opus-5-thinking-high' python -m benches.audittrap
  BENCH_TASKS='claim_battery,fix_baseexception' python -m benches.audittrap
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
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
from benches.audittrap.tasks import (  # noqa: E402
    CLAIMS,
    FIX_TASK_IDS,
    TASK_IDS,
    WONTFIX_TASK_IDS,
    Task,
    build_tasks,
    gold_patch,
    gold_wontfix,
    grade_claim_battery,
    grade_patch,
    grade_wontfix,
    prompt_for_provider,
    run_private_pytest,
)
from benches.audittrap.tools import (  # noqa: E402
    ToolSession,
    extract_patch,
    fresh_fixture_copy,
)

OUT_DIR = results_dir("audittrap")

SELFTEST = os.environ.get("BENCH_SELFTEST") == "1"
PROVIDER = os.environ.get("BENCH_PROVIDER", "ollama").strip().lower()
MODEL = "selftest" if SELFTEST else os.environ.get("BENCH_MODEL", "")
_TAG_BASE = re.sub(r"[^a-zA-Z0-9._-]", "_", MODEL or "model")
TAG = os.environ.get(
    "BENCH_TAG",
    "selftest_audittrap"
    if SELFTEST
    else f"{'cursor_' if PROVIDER in ('cursor', 'cursor-cli', 'agent') else ''}{_TAG_BASE}_audittrap",
)
THINK = parse_think()
OPTIONS = {
    "temperature": float(os.environ.get("BENCH_TEMPERATURE", "0.1")),
    "num_ctx": int(os.environ.get("BENCH_NUM_CTX", "65536")),
    "num_predict": default_num_predict(8192, think_base=24576),
}

MAX_ROUNDS = int(os.environ.get("BENCH_MAX_ROUNDS", "40"))
MAX_TOOL_CALLS = int(os.environ.get("BENCH_MAX_TOOL_CALLS", "40"))
FINALIZE_AFTER = int(os.environ.get("BENCH_FINALIZE_AFTER", "0"))


def _load_local_system_prompt() -> str | None:
    """System message for Ollama/OpenAI-local runs only (not Cursor).

    Disable with ``BENCH_SYSTEM_PROMPT=0``. Override path via
    ``BENCH_SYSTEM_PROMPT_FILE``.
    """
    raw = os.environ.get("BENCH_SYSTEM_PROMPT", "1").strip().lower()
    if raw in ("0", "false", "off", "no", ""):
        return None
    path = Path(
        os.environ.get(
            "BENCH_SYSTEM_PROMPT_FILE",
            str(_ROOT / "system_local.md"),
        )
    )
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


LOCAL_SYSTEM_PROMPT = _load_local_system_prompt()


def _initial_messages(user_prompt: str) -> list[dict[str, str]]:
    msgs: list[dict[str, str]] = []
    if LOCAL_SYSTEM_PROMPT:
        msgs.append({"role": "system", "content": LOCAL_SYSTEM_PROMPT})
    msgs.append({"role": "user", "content": user_prompt})
    return msgs


def chat(
    model: str,
    messages: list[dict[str, str]],
    *,
    think: bool | str | None = None,
    on_thinking=None,
    on_content=None,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    return ollama_chat(
        model,
        messages,
        options=OPTIONS,
        think=THINK if think is None else think,
        timeout_s=timeout_s,
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
                if "--- " in blob and "+++ " in blob:
                    return {"patch": blob}
    dm = _FENCE_DIFF.search(text)
    if dm:
        return {"patch": dm.group(1).strip()}
    m_ans = re.search(r"\{[\s\S]*\"answers\"[\s\S]*\}", text)
    if m_ans:
        try:
            obj = json.loads(m_ans.group(0))
            if isinstance(obj, dict) and "answers" in obj:
                return obj
        except json.JSONDecodeError:
            pass
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("--- a/") or stripped.startswith("+++ b/"):
            return {"patch": "\n".join(lines[i:]).strip()}
    return None


def _result_row(
    task: Task, grade: dict[str, Any], *, provider: str, extra: dict[str, Any]
) -> dict[str, Any]:
    patch = extract_patch(extra.get("answer") or {})
    answer_obj: dict[str, Any] = {}
    raw_answer = extra.get("answer") or {}
    if isinstance(raw_answer, dict):
        answer_obj = dict(raw_answer)
    if patch:
        answer_obj.setdefault("patch", patch)
        answer_obj.setdefault(
            "patch_bytes", grade.get("patch_bytes") or len(patch.encode("utf-8"))
        )
        answer_obj.setdefault(
            "patch_preview", grade.get("patch_preview") or patch[:1200]
        )
        answer_obj.setdefault("apply_detail", grade.get("apply_detail"))
    if "per_claim" in grade:
        answer_obj["answers"] = {
            p["id"]: p.get("got")
            for p in grade.get("per_claim") or []
            if p.get("got") is not None
        }
    return {
        "model": MODEL,
        "provider": provider,
        "bench": "audittrap",
        "task": task.id,
        "title": task.title,
        "family": task.family,
        "ok": bool(grade.get("ok")),
        "score": int(grade.get("score") or 0),
        "max_score": int(grade.get("max_score") or task.max_score),
        "grade_detail": grade.get("detail"),
        "passed": grade.get("passed"),
        "total": grade.get("total"),
        "correct": grade.get("correct"),
        "wrong": grade.get("wrong"),
        "missing": grade.get("missing"),
        "evidence_bonus": grade.get("evidence_bonus"),
        "per_claim": grade.get("per_claim"),
        "answer": answer_obj,
        **{k: v for k, v in extra.items() if k != "answer"},
    }


def run_agent_cursor(task: Task) -> dict[str, Any]:
    from bench_lib import cursor_cli
    from bench_lib.task_timeout import cursor_timeout_s

    work = fresh_fixture_copy()
    try:
        prompt = prompt_for_provider(task.prompt, "cursor")
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
        transcript_path = save_task_transcript(
            OUT_DIR, TAG, task.id, format_think_combined(content, thinking)
        )
        return _result_row(
            task,
            grade,
            provider="cursor",
            extra={
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
                "raw_content": content[:8000],
            },
        )
    finally:
        shutil.rmtree(work.parent, ignore_errors=True)


def run_agent_ollama(task: Task) -> dict[str, Any]:
    from bench_lib.task_timeout import task_timeout_s

    session = ToolSession(max_calls=MAX_TOOL_CALLS)
    messages = _initial_messages(task.prompt)
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
    length_nudges = 0

    for round_i in range(MAX_ROUNDS):
        if time.perf_counter() >= deadline:
            timed_out = True
            totals["done_reason"] = "task_timeout"
            transcript.add_note("TIMEOUT: exceeded BENCH_TASK_TIMEOUT_S")
            break
        totals["rounds"] = round_i + 1
        transcript.begin_round(round_i + 1)
        round_think = think_for_round(round_i, THINK)
        remaining = max(1.0, deadline - time.perf_counter())
        resp = chat(
            MODEL,
            messages,
            think=round_think,
            timeout_s=remaining,
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

        # Soft HTTP/stream failures: treat as task timeout so we don't spin forever.
        if abort_reason in ("http_timeout", "stream_stall", "task_timeout"):
            timed_out = True
            totals["done_reason"] = abort_reason
            transcript.add_note(
                f"TIMEOUT: {abort_reason} (remaining budget / stall exceeded)"
            )
            break

        if abort_reason in ("think_loop", "think_budget") and think_loop_nudges < 2:
            think_loop_nudges += 1
            nudge = think_loop_nudge(thinking=thinking, protocol="repohard")
            transcript.add_note(f"{abort_reason} nudge {think_loop_nudges}/2")
            messages.append({"role": "user", "content": nudge})
            continue

        call = parse_tool_call(content)
        if call is None:
            # Hitting max_tokens without a protocol tag is the classic ds4 hang:
            # endless 8k rants + soft nudges. Force one finalize, then stop.
            if abort_reason == "length" and round_i < MAX_ROUNDS - 1:
                length_nudges += 1
                transcript.add_note(
                    f"LENGTH without protocol ({length_nudges}/2)"
                )
                if length_nudges >= 2:
                    transcript.add_note("LENGTH loop: stopping without final")
                    break
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous reply hit the token limit without a "
                            "valid <arch_tool> or <arch_final>. Stop reasoning. "
                            "Emit ONLY <arch_final>{...}</arch_final> now with "
                            "status patched|unchanged and a short reason."
                        ),
                    }
                )
                continue
            if round_i < MAX_ROUNDS - 1:
                nudge = (
                    "Protocol error: emit one <arch_tool>{...}</arch_tool> "
                    "or <arch_final>{...}</arch_final> for this task."
                )
                transcript.add_note(nudge)
                messages.append({"role": "user", "content": nudge})
                continue
            break

        name = str(call.get("name") or "")
        args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        result = session.dispatch(name, args)
        tool_trace.append(
            {"name": name, "arguments": args, "result_ok": result.get("ok")}
        )
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
                    "content": "Tool budget exhausted. Provide <arch_final> now.",
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
                        "Emit <arch_final> now with your best answer."
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
            "detail": (
                f"TIMEOUT: exceeded BENCH_TASK_TIMEOUT_S; "
                f"partial {grade.get('detail')}"
            ),
            "passed": 0,
        }
    transcript_path = transcript.save()
    return _result_row(
        task,
        grade,
        provider="ollama",
        extra={
            "answer": final or {},
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
            "system_prompt": bool(LOCAL_SYSTEM_PROMPT),
            "raw_content": last_content[:8000],
        },
    )


def run_agent(task: Task) -> dict[str, Any]:
    if PROVIDER in ("cursor", "cursor-cli", "agent"):
        return run_agent_cursor(task)
    if PROVIDER != "ollama":
        raise SystemExit(f"Unknown BENCH_PROVIDER={PROVIDER!r} (use ollama|cursor)")
    return run_agent_ollama(task)


def run_selftest() -> int:
    session = ToolSession(max_calls=MAX_TOOL_CALLS)
    assert session.dispatch("read_file", {"path": "../private/gold/x.patch"})["ok"] is False
    assert session.dispatch("list_dir", {"path": "."})["ok"] is True
    assert session.dispatch("read_file", {"path": "runner.py"})["ok"] is True

    fails: list[str] = []

    for p in [
        "runner.py",
        "chat/wrap.py",
        "chat/facade.py",
        "chat/api.py",
        "solver/sat.py",
        "solver/sql.py",
        "warmup.py",
        "util/subprocess_timeout.py",
        "think/loop.py",
        "scoring.py",
        "schema.py",
        "config.py",
        "compat/__init__.py",
        "pipeline.py",
        "MAINTAINER_NOTES.md",
    ]:
        session.dispatch("read_file", {"path": p})
    gold_answers = {cid: val for cid, _, val in CLAIMS}
    g = grade_claim_battery({"answers": gold_answers}, session)
    if g["correct"] != len(CLAIMS):
        fails.append(f"claim_battery gold {g['correct']}/{len(CLAIMS)}")
    if not g.get("ok"):
        fails.append(f"claim_battery not ok: {g.get('detail')}")

    for tid in FIX_TASK_IDS:
        work = fresh_fixture_copy()
        try:
            r = run_private_pytest(work, tid)
            if r["ok"]:
                fails.append(f"{tid}: unpatched unexpectedly passed")
        finally:
            shutil.rmtree(work.parent, ignore_errors=True)

        grade = grade_patch({"patch": gold_patch(tid)}, session, tid)
        if not grade.get("ok"):
            fails.append(
                f"{tid}: gold failed ({grade.get('detail')}) "
                f"{(grade.get('pytest_output') or '')[-800:]}"
            )

        bad = grade_patch({"patch": ""}, session, tid)
        if bad.get("ok"):
            fails.append(f"{tid}: empty patch passed")

    for tid in WONTFIX_TASK_IDS:
        work = fresh_fixture_copy()
        try:
            r = run_private_pytest(work, tid)
            if not r["ok"]:
                fails.append(
                    f"{tid}: pristine pytest failed: {(r.get('output') or '')[-600:]}"
                )
        finally:
            shutil.rmtree(work.parent, ignore_errors=True)

        good = grade_wontfix(gold_wontfix(tid), session, tid)
        if int(good.get("score") or 0) < 10:
            fails.append(
                f"{tid}: gold wontfix score={good.get('score')} ({good.get('detail')})"
            )

        if tid == "sat_assign_cleared":
            evil = (
                "--- a/solver/sat.py\n"
                "+++ b/solver/sat.py\n"
                "@@ -58,9 +58,6 @@\n"
                "             snap = dict(assign)\n"
                "             if dpll([c[:] for c in cls]):\n"
                "                 return True\n"
                "-            assign.clear()\n"
                "-            assign.update(snap)\n"
                "-            del assign[v]\n"
                "+            del assign[v]\n"
                "         return False\n"
            )
            evil_g = grade_wontfix(
                {"status": "patched", "patch": evil}, session, tid
            )
            if evil_g.get("ok") or int(evil_g.get("score") or 0) > 0:
                fails.append(f"{tid}: evil patch scored {evil_g.get('score')}")

    if fails:
        print("SELFTEST FAILED:", *fails, sep="\n  ", file=sys.stderr)
        return 1
    print(
        "SELFTEST OK",
        json.dumps(
            {"tasks": len(TASK_IDS), "claims": len(CLAIMS), "fixes": len(FIX_TASK_IDS)}
        ),
    )
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
    # Explicit BENCH_TASKS always re-runs (don't skip timed-out/failed rows).
    force_rerun = {
        x.strip()
        for x in os.environ.get("BENCH_TASKS", "").split(",")
        if x.strip()
    }
    with out_log.open("a", encoding="utf-8") as log:
        log.write(f"\n==== audittrap provider={PROVIDER} {MODEL} tag={TAG} {stamp} ====\n")
        for t in tasks:
            if merge_latest and t.id in done_ids and t.id not in force_rerun:
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
                    detail = (
                        f"TIMEOUT: exceeded BENCH_TASK_TIMEOUT_S / Cursor timeout ({e})"
                    )
                    # Salvage whatever Cursor buffered before the kill.
                    partial = ""
                    if isinstance(e, subprocess.TimeoutExpired):
                        out = e.stdout
                        if isinstance(out, str):
                            partial = out
                        elif isinstance(out, (bytes, bytearray)):
                            partial = bytes(out).decode("utf-8", errors="replace")
                    if partial.strip():
                        tp = save_task_transcript(
                            OUT_DIR, TAG, t.id, partial[-50000:]
                        )
                        detail += f"; partial transcript={tp}"
                        r = {
                            "model": MODEL,
                            "provider": PROVIDER,
                            "bench": "audittrap",
                            "task": t.id,
                            "title": t.title,
                            "ok": False,
                            "score": 0,
                            "max_score": t.max_score,
                            "grade_detail": detail,
                            "done_reason": "task_timeout",
                            "transcript": str(tp),
                            "raw_content": partial[-8000:],
                        }
                    else:
                        r = {
                            "model": MODEL,
                            "provider": PROVIDER,
                            "bench": "audittrap",
                            "task": t.id,
                            "title": t.title,
                            "ok": False,
                            "score": 0,
                            "max_score": t.max_score,
                            "grade_detail": detail,
                            "done_reason": "task_timeout",
                        }
                else:
                    r = {
                        "model": MODEL,
                        "provider": PROVIDER,
                        "bench": "audittrap",
                        "task": t.id,
                        "title": t.title,
                        "ok": False,
                        "score": 0,
                        "max_score": t.max_score,
                        "grade_detail": detail,
                        "done_reason": "error",
                    }
            results = [x for x in results if x.get("task") != t.id]
            results.append(r)
            slim = {
                k: r[k]
                for k in r
                if k not in ("tool_trace", "pytest_output", "per_claim", "raw_content")
            }
            print(json.dumps(slim, indent=2))
            log.write(json.dumps(r, indent=2) + "\n")
            out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
            latest_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    total = sum(int(r.get("score") or 0) for r in results)
    mx = sum(int(r.get("max_score") or 0) for r in results)
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
    (OUT_DIR / f"{TAG}_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
