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

from bench_lib.ollama_think import apply_think, default_num_predict, parse_think  # noqa: E402
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
from benches.repohard.tools import FIXTURE_ROOT, ToolSession, fresh_fixture_copy  # noqa: E402

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
FIXTURE = FIXTURE_ROOT

THINK = parse_think()
OPTIONS = {
    "temperature": float(os.environ.get("BENCH_TEMPERATURE", "0.1")),
    "num_ctx": int(os.environ.get("BENCH_NUM_CTX", "65536")),
    "num_predict": default_num_predict(8192, think_base=24576),
}

MAX_ROUNDS = int(os.environ.get("BENCH_MAX_ROUNDS", "40"))
MAX_TOOL_CALLS = int(os.environ.get("BENCH_MAX_TOOL_CALLS", "40"))
HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def chat(model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "stream": False,
        "messages": messages,
        "options": OPTIONS,
    }
    apply_think(body, THINK)
    data_bytes = json.dumps(body).encode()
    t0 = time.perf_counter()
    last_err: Exception | None = None
    data: dict[str, Any] | None = None
    for attempt in range(1, 6):
        req = urllib.request.Request(
            f"{HOST}/api/chat",
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=3600) as resp:
                data = json.loads(resp.read().decode())
            break
        except urllib.error.HTTPError as e:
            last_err = e
            body_err = e.read().decode("utf-8", errors="replace")
            if e.code == 500 and "EOF" in body_err and attempt >= 2:
                break
            time.sleep(min(30, 2**attempt))
        except urllib.error.URLError as e:
            last_err = e
            time.sleep(min(30, 2**attempt))
    if data is None:
        raise last_err or RuntimeError("chat failed")
    wall = time.perf_counter() - t0
    msg = data.get("message") or {}
    content = msg.get("content") or ""
    eval_duration = float(data.get("eval_duration") or 0)
    eval_count = float(data.get("eval_count") or 0)
    return {
        "content": content,
        "thinking": msg.get("thinking") or "",
        "wall_s": wall,
        "load_s": float(data.get("load_duration") or 0) / 1e9,
        "prompt_tokens": int(data.get("prompt_eval_count") or 0),
        "eval_tokens": int(data.get("eval_count") or 0),
        "toks_per_s": (eval_count / (eval_duration / 1e9)) if eval_duration > 0 else 0.0,
        "done_reason": data.get("done_reason"),
        "raw": data,
    }


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
    if "--- a/" in text and "+++ b/" in text:
        start = text.find("--- ")
        return {"patch": text[start:].strip()}
    return None


def run_agent_cursor(task: Task) -> dict[str, Any]:
    from bench_lib import cursor_cli

    prompt = prompt_for_provider(task.prompt, "cursor")
    resp = cursor_cli.chat(
        MODEL,
        prompt,
        mode=os.environ.get("BENCH_CURSOR_MODE", "ask"),
        workspace=FIXTURE,
    )
    content = resp.get("content") or ""
    final = parse_final_answer(content) or {}
    session = ToolSession(max_calls=MAX_TOOL_CALLS)
    assert task.grade is not None
    grade = task.grade(final, session)
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
            "patch_bytes": grade.get("patch_bytes"),
            "patch_preview": grade.get("patch_preview"),
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
        "session_id": resp.get("session_id"),
        "raw_content": content[:8000],
    }


def run_agent_ollama(task: Task) -> dict[str, Any]:
    session = ToolSession(max_calls=MAX_TOOL_CALLS)
    messages = [{"role": "user", "content": task.prompt}]
    tool_trace: list[dict[str, Any]] = []
    totals = {
        "wall_s": 0.0,
        "prompt_tokens": 0,
        "eval_tokens": 0,
        "rounds": 0,
        "done_reason": None,
    }
    final: dict[str, Any] | None = None
    last_content = ""

    for round_i in range(MAX_ROUNDS):
        totals["rounds"] = round_i + 1
        resp = chat(MODEL, messages)
        totals["wall_s"] += resp["wall_s"]
        totals["prompt_tokens"] += resp["prompt_tokens"]
        totals["eval_tokens"] += resp["eval_tokens"]
        totals["done_reason"] = resp["done_reason"]
        content = resp["content"] or ""
        last_content = content
        messages.append({"role": "assistant", "content": content})

        final = parse_final_answer(content)
        if final is not None and parse_tool_call(content) is None:
            break

        call = parse_tool_call(content)
        if call is None:
            if round_i < MAX_ROUNDS - 1:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Protocol error: emit either one <arch_tool>{...}</arch_tool> "
                            'or a <arch_final>{"patch": "...unified diff..."}</arch_final>.'
                        ),
                    }
                )
                continue
            break

        name = str(call.get("name") or "")
        args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        result = session.dispatch(name, args)
        tool_trace.append({"name": name, "arguments": args, "result_ok": result.get("ok")})
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

    assert task.grade is not None
    grade = task.grade(final or {}, session)
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
        "answer": {"patch_bytes": grade.get("patch_bytes")},
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
    results: list[dict[str, Any]] = []

    try:
        if PROVIDER in ("cursor", "cursor-cli", "agent"):
            from bench_lib import cursor_cli

            cursor_cli.chat(
                MODEL,
                "Reply with the single word: pong",
                mode="ask",
                workspace=FIXTURE,
            )
        else:
            chat(MODEL, [{"role": "user", "content": "Reply with the single word: pong"}])
    except Exception as e:  # noqa: BLE001
        print(f"warmup failed: {e}", file=sys.stderr)
        return 2

    with out_log.open("a", encoding="utf-8") as log:
        log.write(f"\n==== repohard provider={PROVIDER} {MODEL} tag={TAG} {stamp} ====\n")
        for t in tasks:
            print(f"-- {t.id} ...", flush=True)
            log.write(f"-- {t.id} ...\n")
            try:
                r = run_agent(t)
            except Exception as e:  # noqa: BLE001
                r = {
                    "model": MODEL,
                    "provider": PROVIDER,
                    "task": t.id,
                    "title": t.title,
                    "ok": False,
                    "score": 0,
                    "max_score": t.max_score,
                    "grade_detail": f"ERROR: {type(e).__name__}: {e}",
                }
            results.append(r)
            print(json.dumps({k: r[k] for k in r if k not in ("tool_trace", "pytest_output")}, indent=2))
            log.write(json.dumps(r, indent=2) + "\n")
            out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
            (OUT_DIR / f"{TAG}_latest.json").write_text(
                json.dumps(results, indent=2), encoding="utf-8"
            )

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
