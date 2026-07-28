#!/usr/bin/env python3.14
"""Run pyhard tasks against an OpenAI-compatible chat API (e.g. ds4-server).

  DS4_BASE=http://127.0.0.1:8000 BENCH_MODEL=deepseek-v4-flash \\
    python3.14 scripts/run_pyhard_openai.py
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("BENCH_MODEL", "deepseek-v4-flash")
os.environ.setdefault("BENCH_PROVIDER", "openai")
os.environ.setdefault("BENCH_TAG", "ds4_flash_q2imatrix_pyhard")
os.environ.setdefault("BENCH_THINK", "0")

BASE = os.environ.get("DS4_BASE", "http://127.0.0.1:8000").rstrip("/")
MODEL = os.environ["BENCH_MODEL"]
MAX_TOKENS = int(os.environ.get("BENCH_NUM_PREDICT", "8192"))
TEMP = float(os.environ.get("BENCH_TEMPERATURE", "0.1"))

from benches.pyhard import bench as ph  # noqa: E402


def chat_openai(prompt: str) -> dict[str, Any]:
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": TEMP,
        "max_tokens": MAX_TOKENS,
        "stream": False,
        # ds4-server: disable thinking (see ./ds4-server --help thinking)
        "think": False,
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}/v1/chat/completions",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=3600) as resp:
        payload = json.loads(resp.read().decode())
    wall = time.perf_counter() - t0
    msg = (payload.get("choices") or [{}])[0].get("message") or {}
    content = msg.get("content") or ""
    # Some gateways put reasoning separately.
    thinking = msg.get("reasoning_content") or msg.get("thinking") or ""
    usage = payload.get("usage") or {}
    return {
        "content": content,
        "thinking": thinking,
        "wall_s": wall,
        "load_s": 0.0,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "eval_tokens": int(usage.get("completion_tokens") or 0),
        "toks_per_s": (
            float(usage.get("completion_tokens") or 0) / wall if wall > 0 else 0.0
        ),
        "done_reason": (payload.get("choices") or [{}])[0].get("finish_reason")
        or "stop",
        "raw": payload,
        "provider": "openai",
        "think": False,
    }


def main() -> int:
    # Smoke the endpoint first.
    try:
        warm = chat_openai("Reply with exactly: OK")
        preview = repr((warm.get("content") or "")[:80])
        print(f"warmup ok wall={warm['wall_s']:.1f}s content={preview}")
    except Exception as e:
        print(f"warmup FAILED: {e}", file=sys.stderr)
        return 1

    out_dir = ph.OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = ph.TAG
    stamp = time.strftime("%Y%m%d_%H%M%S")
    results_path = out_dir / f"{tag}_pyhard_{stamp}.json"
    latest_path = out_dir / f"{tag}_pyhard_latest.json"
    log_path = out_dir / f"{tag}_pyhard_{stamp}.log"

    tasks = [t for t in ph.TASKS if not ph._TASK_FILTER or t.id in ph._TASK_FILTER]
    results: list[dict[str, Any]] = []
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"provider=openai base={BASE} model={MODEL} tag={tag}\n")
        for task in tasks:
            print(f"-- {task.id} ...", flush=True)
            log.write(f"-- {task.id} ...\n")
            try:
                resp = chat_openai(task.prompt)
                text = ph.grade_from_response(
                    resp.get("content") or "",
                    resp.get("thinking") or "",
                    scrape_thinking=False,
                )
                g = task.grade(text)
                row = {
                    "model": MODEL,
                    "provider": "openai",
                    "base": BASE,
                    "task": task.id,
                    "title": task.title,
                    "ok": g["ok"],
                    "score": g["score"],
                    "max_score": g["max_score"],
                    "grade_detail": g["detail"],
                    "wall_s": round(resp.get("wall_s") or 0, 2),
                    "eval_tokens": int(resp.get("eval_tokens") or 0),
                    "prompt_tokens": int(resp.get("prompt_tokens") or 0),
                    "toks_per_s": round(resp.get("toks_per_s") or 0, 2),
                    "done_reason": resp.get("done_reason", "unknown"),
                    "content_chars": len(resp.get("content") or ""),
                    "code_chars": len(g.get("code") or ""),
                }
                (out_dir / f"{tag}__{task.id}__code.py").write_text(
                    g.get("code") or "", encoding="utf-8"
                )
            except Exception as e:
                row = {
                    "model": MODEL,
                    "provider": "openai",
                    "task": task.id,
                    "ok": False,
                    "score": 0,
                    "max_score": task.max_score,
                    "error": str(e),
                }
            results.append(row)
            print(json.dumps({k: row[k] for k in row if k != "grade_detail"}, indent=2))
            log.write(json.dumps(row, indent=2) + "\n")
            results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    latest_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    total = sum(r.get("score") or 0 for r in results)
    mx = sum(r.get("max_score") or 0 for r in results)
    ok_n = sum(1 for r in results if r.get("ok"))
    print(f"SUMMARY score={total}/{mx} ok_tasks={ok_n}/{len(results)} -> {latest_path}")
    return 0 if ok_n == len(results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
