#!/usr/bin/env python3
"""Does the speedup of the draft-equipped models decay as context grows?

Background. gemma4:31b-mlx-bf16 and gemma4:31b-coding-mtp-bf16 share 1245 of 1247 layer
digests: they are one checkpoint carrying an embedded ~0.4B draft network (48 draft.model.*
tensors, 31.7B against the plain model's 31.3B), served by two different runners. So the
3x decode advantage over gemma4:31b-it-bf16 is speculative decoding, not a faster backend
and not a wider memory bus.

That reframes two anomalies. During an unattended chain the MLX arm fell from 30 tok/s to
3.8 -- below the plain dense model it was chosen to beat -- and two benches came out slower
on MLX despite identical scores. Both are what speculation looks like when the draft stops
being accepted: you pay for the draft passes and the verification, and net lose. If that is
the cause, the loss should track context length, because a draft network predicting token
t+2 from a 100k conversation has a harder job than from a 500-token one.

Method. One fixed generation task at several context sizes, on three models sharing a pinned
window: the plain dense model as a baseline that has no draft to lose, and the same draft
checkpoint on both runners. Decode rate comes from eval_count/eval_duration, which excludes
prefill, so growing the prompt does not mechanically depress the number. The derived figure
is draft rate / dense rate at each size: the speculative multiplier actually being realised.
If it is flat, context is not the variable and the decay is something else.

Usage: speculation_acceptance_probe.py [64k|128k]
"""
from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

HOST = "http://127.0.0.1:11434"

WINDOWS = {
    # Dense first so its numbers exist before anything can go wrong with the 63GB arms.
    "64k": (
        [
            ("dense  llama.cpp", "gemma4-31b-coding-64k", False),
            ("draft  llama.cpp", "gemma4-31b-mtp-64k", True),
            ("draft  MLX", "gemma4-31b-mlx-64k", True),
        ],
        [400, 4000, 8000, 16000, 32000],
    ),
    # 56000 rather than 64000 at the top: the prompt, the 256 generated tokens and the
    # template all have to fit inside 65536 on the 64k arms, and a probe that silently
    # truncates its own prefix would measure the wrong thing.
    "128k": (
        [
            ("dense  llama.cpp", "gemma4-31b-coding-128k", False),
            ("draft  llama.cpp", "gemma4-31b-mtp-128k", True),
            ("draft  MLX", "gemma4-31b-mlx-128k", True),
        ],
        [32000, 64000, 96000, 120000],
    ),
}

TASK = (
    "Ignore the material above. Write a complete, production-quality Python implementation "
    "of a red-black tree supporting insert, delete, search and in-order traversal, with "
    "full docstrings and type hints on every method."
)

TRIALS = 2
NUM_PREDICT = 256


def post(path: str, payload: dict, timeout: int = 3600) -> dict:
    req = urllib.request.Request(
        HOST + path,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def corpus() -> str:
    """Real source text, so the prefix has the token mix an agent would actually carry."""
    chunks = []
    for pat in ("bench_lib/*.py", "benches/repohard/*.py", "benches/audittrap/*.py",
                "benches/pyhard/*.py", "scripts/*.py", "*.py"):
        for p in sorted(Path(".").glob(pat)):
            try:
                chunks.append(p.read_text(errors="ignore"))
            except Exception:
                continue
    body = "\n\n".join(chunks)
    return body if body else (TASK + "\n") * 2000


CORPUS = corpus()


def prefix(approx_tokens: int) -> str:
    """~3.6 chars per token is close enough; the server reports the real count anyway."""
    if approx_tokens <= 400:
        return ""
    want = int(approx_tokens * 3.6)
    body = CORPUS
    while len(body) < want:
        body += CORPUS
    return body[:want] + "\n\n"


def generate(model: str, prompt: str) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "keep_alive": "45m",
        "options": {"num_predict": NUM_PREDICT},
    }
    t0 = time.time()
    try:
        r = post("/api/chat", payload)
    except Exception as exc:
        return {"error": str(exc)[:160]}
    wall = time.time() - t0
    ec, ed = int(r.get("eval_count") or 0), int(r.get("eval_duration") or 0)
    pc, pd = int(r.get("prompt_eval_count") or 0), int(r.get("prompt_eval_duration") or 0)
    return {
        "wall": wall,
        "prompt_tokens": pc,
        "eval_tokens": ec,
        "decode_tps": ec / (ed / 1e9) if ed else 0.0,
        "prefill_tps": pc / (pd / 1e9) if pd else 0.0,
        "chars": len(r.get("message", {}).get("content") or ""),
    }


def main() -> int:
    window = sys.argv[1] if len(sys.argv) > 1 else "64k"
    if window not in WINDOWS:
        print(f"usage: {sys.argv[0]} [64k|128k]", file=sys.stderr)
        return 2
    models, contexts = WINDOWS[window]

    results: dict[str, dict[int, float]] = {}
    detail: list[dict] = []

    print(f"######## window {window} ########", flush=True)
    for label, model, has_draft in models:
        print(f"\n{'=' * 78}\n{label}   {model}   draft={'yes' if has_draft else 'no'}\n{'=' * 78}",
              flush=True)
        warm = generate(model, "ok")
        if warm.get("error"):
            print(f"  UNAVAILABLE: {warm['error']}", flush=True)
            continue
        results[label] = {}
        for ctx in contexts:
            prompt = prefix(ctx) + TASK
            rates = []
            for t in range(TRIALS):
                g = generate(model, prompt)
                if g.get("error"):
                    print(f"  ctx~{ctx:>7}  trial {t + 1}: ERROR {g['error']}", flush=True)
                    continue
                rates.append(g["decode_tps"])
                print(f"  ctx~{ctx:>7}  trial {t + 1}: prompt {g['prompt_tokens']:>7} tok  "
                      f"decode {g['decode_tps']:>6.2f} tok/s  "
                      f"prefill {g['prefill_tps']:>9.1f} tok/s  "
                      f"{g['eval_tokens']} tok in {g['wall']:.1f}s wall", flush=True)
                detail.append({"window": window, "model": label, "ctx": ctx, "trial": t + 1, **g})
            if rates:
                results[label][ctx] = statistics.median(rates)
        try:
            post("/api/chat", {"model": model, "messages": [], "keep_alive": 0})
        except Exception:
            pass

    print(f"\n{'=' * 78}\nrealised speculative multiplier vs context, window {window}\n{'=' * 78}")
    base = results.get("dense  llama.cpp", {})
    header = f"{'context':>10}  {'dense':>9}"
    for label in ("draft  llama.cpp", "draft  MLX"):
        header += f"  {label.split()[-1] + ' tok/s':>13}  {'xdense':>7}"
    print(header)
    print("-" * len(header))
    for ctx in contexts:
        b = base.get(ctx)
        row = f"{ctx:>10}  {b:>9.2f}" if b else f"{ctx:>10}  {'--':>9}"
        for label in ("draft  llama.cpp", "draft  MLX"):
            d = results.get(label, {}).get(ctx)
            if d and b:
                row += f"  {d:>13.2f}  {d / b:>6.2f}x"
            else:
                row += f"  {'--':>13}  {'--':>7}"
        print(row)

    print()
    print("A multiplier that falls with context is speculation losing acceptance: the draft")
    print("proposes, the target rejects, and both passes are paid for. Below 1.00x the draft")
    print("is pure overhead and the plain dense model is the faster choice.")

    dest = Path(f"results/speculation_acceptance_{window}.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"median": results, "detail": detail}, indent=2))
    print(f"\nwrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
