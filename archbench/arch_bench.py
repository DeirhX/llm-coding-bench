#!/usr/bin/env python3.14
"""Tools-first architecture / call-chain benchmark for local Ollama models.

Usage:
  BENCH_SELFTEST=1 python3.14 arch_bench.py
  BENCH_MODEL='qwen3-coder-next:q8_0' python3.14 arch_bench.py
  BENCH_MODEL='...' BENCH_TAG='next_arch' BENCH_TASKS='chain_delete_order,tenant_invoice_isolation' python3.14 arch_bench.py
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
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tasks import SELFTEST_TRAJECTORIES, Task, build_tasks  # noqa: E402
from tools import ToolSession  # noqa: E402

OUT_DIR = Path.home() / ".ollama" / "bench" / "results" / "archbench"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SELFTEST = os.environ.get("BENCH_SELFTEST") == "1"
MODEL = "selftest" if SELFTEST else os.environ.get("BENCH_MODEL", "")
TAG = os.environ.get(
    "BENCH_TAG",
    "selftest_arch" if SELFTEST else re.sub(r"[^a-zA-Z0-9._-]", "_", MODEL or "model") + "_arch",
)

OPTIONS = {
    "temperature": float(os.environ.get("BENCH_TEMPERATURE", "0.1")),
    "num_ctx": int(os.environ.get("BENCH_NUM_CTX", "65536")),
    "num_predict": int(os.environ.get("BENCH_NUM_PREDICT", "8192")),
}

MAX_ROUNDS = int(os.environ.get("BENCH_MAX_ROUNDS", "32"))
MAX_TOOL_CALLS = int(os.environ.get("BENCH_MAX_TOOL_CALLS", "30"))
HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def chat(model: str, messages: list[dict[str, str]]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "stream": False,
        "messages": messages,
        "options": OPTIONS,
    }
    # Disable extended thinking for agent loops (saves budget; thinking models still work).
    # Override with BENCH_THINK=1 if you want thinking on.
    if os.environ.get("BENCH_THINK", "0") != "1":
        body["think"] = False
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
            # Protocol/parser poison (Qwen tool_call EOF) — fail fast after one retry
            if e.code == 500 and "EOF" in body_err and attempt >= 2:
                break
            # Ollama often 500s while swapping large models; back off and retry.
            time.sleep(min(30, 2 ** attempt))
        except urllib.error.URLError as e:
            last_err = e
            time.sleep(min(30, 2 ** attempt))
    if data is None:
        raise last_err or RuntimeError("chat failed")
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
    }


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
        if m3 and ("chain" in m3.group(0) or "citations" in m3.group(0) or "root_cause" in m3.group(0) or "touch_files" in m3.group(0) or "violated" in m3.group(0) or "findings" in m3.group(0) or "enforced_at" in m3.group(0)):
            blob = m3.group(0)
    if not blob:
        return None
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
        if resp["thinking"] and not content.strip():
            content = resp["thinking"]
        last_content = content
        messages.append({"role": "assistant", "content": content})

        final = parse_final_answer(content)
        if final is not None and parse_tool_call(content) is None:
            break

        call = parse_tool_call(content)
        if call is None:
            # nudge once if model forgot protocol
            if round_i < MAX_ROUNDS - 1:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Protocol error: emit either one <arch_tool>{...}</arch_tool> "
                            "or a <arch_final>{...}</arch_final> JSON object. No other chatter."
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
                    "content": "Tool budget exhausted. Provide <arch_final> JSON now.",
                }
            )

    grade = task.grade(final or {}, session)
    return {
        "model": MODEL,
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
    }


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
    results: list[dict[str, Any]] = []

    # warmup
    try:
        chat(MODEL, [{"role": "user", "content": "Reply with the single word: pong"}])
    except Exception as e:  # noqa: BLE001
        print(f"warmup failed: {e}", file=sys.stderr)
        return 2

    with out_log.open("a", encoding="utf-8") as log:
        log.write(f"\n==== archbench {MODEL} tag={TAG} {stamp} ====\n")
        for t in tasks:
            print(f"-- {t.id} ...", flush=True)
            log.write(f"-- {t.id} ...\n")
            try:
                r = run_agent_ollama(t)
            except Exception as e:  # noqa: BLE001
                r = {
                    "model": MODEL,
                    "task": t.id,
                    "title": t.title,
                    "ok": False,
                    "score": 0,
                    "max_score": t.max_score,
                    "grade_detail": f"ERROR: {type(e).__name__}: {e}",
                }
            results.append(r)
            print(json.dumps({k: r[k] for k in r if k not in ("tool_trace", "answer")}, indent=2))
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
