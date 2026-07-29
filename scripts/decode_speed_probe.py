#!/usr/bin/env python3
"""Separate prefill throughput from decode throughput, because they have different cures.

The repohard transcripts show 21 prompt tokens per generated token on the 31B, which
looks like prompt processing dominates until you remember Ollama caches the prefix
across rounds: only the delta is actually computed. Aggregate wall-clock therefore
cannot tell you which half is expensive. Ollama's own counters can -- every response
carries prompt_eval_count/prompt_eval_duration and eval_count/eval_duration -- so this
asks the server directly instead of inferring it.

Why it matters here: decode on a dense model is memory-bandwidth-bound and scales with
bytes-per-weight, so quantization fixes it. Prefill is compute-bound and does not.
Reporting one number for "speed" would hide which lever applies.

Two prompt sizes per model. The short arm is a fresh small context; the long arm
prepends several thousand tokens of real source so prefill has something to chew on,
which is the regime an agent in a real repo actually lives in.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").strip()
if not HOST.startswith("http"):
    HOST = "http://" + HOST

# This machine: M5 Max, 40-core GPU, 128GB, 614.4 GB/s of unified memory bandwidth.
# A dense model reads every weight once per token, so decode tok/s x weight bytes is
# the effective bandwidth achieved, and the fraction of this number says whether the
# model is bus-bound (nothing but fewer bytes will help) or leaving headroom.
PEAK_GBPS = 614.4

TASK = (
    "Write a complete, production-quality Python implementation of a red-black tree "
    "supporting insert, delete, search, and in-order traversal. Include full docstrings "
    "and type hints on every method, and explain the rebalancing cases in comments."
)


def post(path: str, payload: dict, timeout: int = 1800) -> dict:
    req = urllib.request.Request(
        HOST + path,
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def vram_bytes(model: str) -> int:
    """Bytes the loaded model actually occupies, straight from the server.

    Read after loading rather than taken from the tag name, so a mislabelled or
    unexpectedly-quantized blob cannot silently corrupt the bandwidth arithmetic.
    """
    try:
        for m in _ps():
            if m.get("name") == model or m.get("model") == model:
                return int(m.get("size_vram") or m.get("size") or 0)
    except Exception:
        pass
    return 0


def _ps() -> list:
    with urllib.request.urlopen(HOST + "/api/ps", timeout=30) as resp:
        return json.loads(resp.read().decode()).get("models", [])


def filler(approx_tokens: int) -> str:
    """Real source text, so prefill sees the token mix an agent would actually send."""
    chunks = []
    for p in sorted(Path("bench_lib").glob("*.py")) + sorted(Path("benches/repohard").glob("*.py")):
        try:
            chunks.append(p.read_text(errors="ignore"))
        except Exception:
            continue
    body = "\n\n".join(chunks)
    if not body:
        body = (TASK + "\n") * 500
    # ~3.6 chars per token is close enough for sizing; the exact count is reported by
    # the server anyway, so this only needs to land in the right order of magnitude.
    want = int(approx_tokens * 3.6)
    while len(body) < want:
        body += body
    return body[:want]


def run(model: str, prompt: str, num_predict: int, num_ctx: int, label: str) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "keep_alive": "30m",
        # No temperature: the model's own Modelfile sampler applies, matching the
        # BENCH_TEMPERATURE=auto conditions every score in this repo was measured under.
        "options": {"num_predict": num_predict, "num_ctx": num_ctx},
    }
    t0 = time.time()
    try:
        r = post("/api/chat", payload)
    except Exception as exc:
        return {"label": label, "error": str(exc)[:200]}
    wall = time.time() - t0
    ec = int(r.get("eval_count") or 0)
    ed = int(r.get("eval_duration") or 0)
    pc = int(r.get("prompt_eval_count") or 0)
    pd = int(r.get("prompt_eval_duration") or 0)
    ld = int(r.get("load_duration") or 0)
    return {
        "label": label,
        "wall_s": wall,
        "prompt_tokens": pc,
        "eval_tokens": ec,
        "decode_tps": (ec / (ed / 1e9)) if ed else 0.0,
        "prefill_tps": (pc / (pd / 1e9)) if pd else 0.0,
        "load_s": ld / 1e9,
        "done_reason": r.get("done_reason"),
    }


def probe(model: str, trials: int = 3) -> dict:
    print(f"\n==== {model} ====", flush=True)
    warm = run(model, "ok", 1, 8192, "warm")
    if warm.get("error"):
        print(f"  UNAVAILABLE: {warm['error']}")
        return {"model": model, "error": warm["error"]}
    size = vram_bytes(model)
    print(f"  resident: {size / 1e9:.1f} GB  (load {warm.get('load_s', 0):.1f}s)")

    long_prompt = filler(8000) + "\n\n" + TASK
    results = {"model": model, "size_gb": size / 1e9, "short": [], "long": []}
    for i in range(trials):
        s = run(model, TASK, 256, 8192, f"short#{i + 1}")
        results["short"].append(s)
        print(f"  short#{i + 1}: prefill {s.get('prefill_tps', 0):>8.1f} tok/s "
              f"({s.get('prompt_tokens', 0)} tok)   decode {s.get('decode_tps', 0):>7.2f} tok/s "
              f"({s.get('eval_tokens', 0)} tok)  {s.get('done_reason')}", flush=True)
    for i in range(trials):
        l = run(model, long_prompt, 256, 32768, f"long#{i + 1}")
        results["long"].append(l)
        print(f"  long#{i + 1}:  prefill {l.get('prefill_tps', 0):>8.1f} tok/s "
              f"({l.get('prompt_tokens', 0)} tok)   decode {l.get('decode_tps', 0):>7.2f} tok/s "
              f"({l.get('eval_tokens', 0)} tok)  {l.get('done_reason')}", flush=True)

    dec = [r["decode_tps"] for r in results["short"] if r.get("decode_tps")]
    if dec and size:
        med = statistics.median(dec)
        eff = med * size / 1e9
        print(f"  median decode {med:.2f} tok/s x {size / 1e9:.1f} GB = {eff:.0f} GB/s "
              f"= {100 * eff / PEAK_GBPS:.0f}% of this machine's {PEAK_GBPS:.0f} GB/s ceiling")
        results["effective_gbps"] = eff
    try:
        post("/api/chat", {"model": model, "messages": [], "keep_alive": 0})
    except Exception:
        pass
    return results


def main() -> int:
    models = sys.argv[1:]
    if not models:
        print("usage: decode_speed_probe.py MODEL [MODEL ...]", file=sys.stderr)
        return 2
    out = [probe(m) for m in models]
    dest = Path("results/decode_speed_probe.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    prev = []
    if dest.exists():
        try:
            prev = json.loads(dest.read_text())
        except Exception:
            prev = []
    prev.extend(out)
    dest.write_text(json.dumps(prev, indent=2))
    print(f"\nwrote {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
