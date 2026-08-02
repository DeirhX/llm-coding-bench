#!/usr/bin/env python3
"""Score the speed variants against the bf16 31B on both axes: throughput and judgement.

Kept out of speed_variant_gate.sh because zsh reads a script incrementally as it runs,
so editing the gate mid-flight would corrupt a run already in progress.

One correction over the gate's inline table. The probe fires three long-prompt trials
per model, and Ollama serves trials 2 and 3 from the prefix cache: it still reports the
full prompt_eval_count but a near-zero duration, which comes out as ~59,000 tok/s of
"prefill". That is a cache hit being reported as compute. Only the first long trial has
a cold prefix, so that is the one taken here. The 59k figure is not meaningless -- it is
precisely why decode dominates an agent loop, since after the first turn the stable
prefix costs nothing and only the newly appended tokens are actually processed.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

PEAK_GBPS = 614.4
TRAPS = {"sat_assign_cleared", "sql_where_inside_join"}
FIXES = {"runner_interrupt_scored", "chat_timeout_dropped",
         "subprocess_stderr_dropped", "warmup_no_deadline"}

# The dense flag is not cosmetic. Bytes-read-per-token equals the resident size only for
# a dense model; a mixture-of-experts touches one slice of its weights per token, so
# applying the same arithmetic to the 26B produced "490% of the memory bandwidth
# ceiling" -- which is not a fast model, it is a wrong formula. For the sparse one the
# useful quantity is inverted instead: given the bus efficiency the dense model
# demonstrates, how many gigabytes must it be reading to go that fast?
VARIANTS = [
    ("31B bf16 (deployed)", "gemma4-coding:31b", True,
     "gemma4-coding_31b_audittrap_skeptic_min",
     "gemma4-coding_31b_repohard_np24576_rep1"),
    ("31B QAT ~4bit", "gemma4-coding:31b-qat", True,
     "gemma4-coding_31b-qat_audittrap_skeptic_min",
     "gemma4-coding_31b-qat_repohard_np24576"),
    ("31B MTP bf16", "gemma4-coding:31b-mtp", True,
     "gemma4-coding_31b-mtp_audittrap_skeptic_min",
     "gemma4-coding_31b-mtp_repohard_np24576"),
    ("31B q8_0", "gemma4-coding:31b-q8", True,
     "gemma4-coding_31b-q8_audittrap_skeptic_min",
     "gemma4-coding_31b-q8_repohard_np24576"),
    ("31B MLX runtime", "gemma4-coding:31b-mlx", True,
     "gemma4-coding_31b-mlx_audittrap_skeptic_min",
     "gemma4-coding_31b-mlx_repohard_np24576"),
    ("26B-A4B bf16 (ref)", "gemma4-coding:26b-a4b", False,
     "gemma4-coding_26b-a4b_audittrap_skeptic_min",
     "gemma4-coding_26b-a4b_repohard_np24576_rep2"),
]


def load(kind: str, tag: str):
    p = Path(f"results/{kind}/{tag}_latest.json")
    if not p.exists():
        return None
    rows = json.loads(p.read_text())
    return rows if isinstance(rows, list) else [rows]


def speeds() -> dict:
    out = {}
    p = Path("results/decode_speed_probe.json")
    if not p.exists():
        return out
    for entry in json.loads(p.read_text()):
        if entry.get("error"):
            continue
        dec = [r["decode_tps"] for r in entry.get("short", []) + entry.get("long", [])
               if r.get("decode_tps")]
        longs = [r for r in entry.get("long", []) if r.get("prefill_tps")]
        cold = longs[0]["prefill_tps"] if longs else 0.0
        cached = max((r["prefill_tps"] for r in longs), default=0.0)
        if dec:
            out[entry["model"]] = {
                "decode": statistics.median(dec),
                "cold_prefill": cold,
                "cached_prefill": cached,
                "gb": entry.get("size_gb") or 0.0,
            }
    return out


def main() -> int:
    speed = speeds()
    print()
    print("==== is there a faster way to run the 31B that is still the same model? ====")
    print("Speed is the easy half. The gate is whether judgement survives: 20/20 traps and")
    print("38/40 fixes is the bf16 31B under the 63-word skeptic_min prompt, and the trap")
    print("column is the one that makes it worth running at all.")
    print()
    h = (f"{'variant':22}{'GB':>6}{'decode':>9}{'vs bf16':>9}{'%bus':>7}"
         f"{'prefill':>9}{'traps':>8}{'fixes':>8}{'claims':>8}{'repohard':>10}")
    print(h)
    print("-" * len(h))

    base = speed.get("gemma4-coding:31b", {}).get("decode") or None
    # Bus efficiency demonstrated by the dense reference, used to infer what the sparse
    # model must be reading per token.
    ref = speed.get("gemma4-coding:31b", {})
    eff = (ref.get("decode", 0) * ref.get("gb", 0) / PEAK_GBPS) if ref else 0.9

    for label, model, dense, atag, rtag in VARIANTS:
        s = speed.get(model, {})
        dec, gb = s.get("decode", 0.0), s.get("gb", 0.0)
        ratio = f"{dec / base:.2f}x" if (base and dec) else "-"
        if not dec:
            bus = "-"
        elif dense:
            bus = f"{100 * dec * gb / PEAK_GBPS:.0f}%"
        else:
            bus = f"~{eff * PEAK_GBPS / dec:.0f}GB"

        rows = load("audittrap", atag)

        def bucket(keep):
            if not rows:
                return "-"
            got = sum(int(r.get("score") or 0) for r in rows if r.get("task") in keep)
            mx = sum(int(r.get("max_score") or 0) for r in rows if r.get("task") in keep)
            return f"{got}/{mx}" if mx else "-"

        rr = load("repohard", rtag)
        repo = f"{sum(int(r.get('score') or 0) for r in rr)}/80" if rr else "-"
        print(f"{label:22}{gb:>6.0f}{dec:>9.2f}{ratio:>9}{bus:>7}"
              f"{s.get('cold_prefill', 0):>9.0f}{bucket(TRAPS):>8}{bucket(FIXES):>8}"
              f"{bucket({'claim_battery'}):>8}{repo:>10}")

    print()
    print("%bus is decode tok/s x resident GB against 614 GB/s of unified memory bandwidth.")
    print("Anything near 90% is reading weights as fast as the hardware permits, so its")
    print("only remaining lever is carrying fewer bytes -- not configuration. For the")
    print("sparse model that column instead shows the weight slice its speed implies it")
    print("is actually reading, which is far less than the memory it occupies.")
    print()
    print("A dense model above 100% is not an error: it is emitting more tokens than one")
    print("pass over its weights can produce, which is exactly what multi-token prediction")
    print("does. That figure is the acceptance rate of the speculative heads, and it is the")
    print("only direct evidence available that Ollama engaged them at all.")
    print()
    for label, model, _, _, _ in VARIANTS:
        s = speed.get(model)
        if not s:
            continue
        print(f"  {label:22} cold prefill {s['cold_prefill']:>7.0f} tok/s   "
              f"cached prefix {s['cached_prefill']:>9.0f} tok/s effective")
    print()
    print("The gap between those two columns is why decode is the constraint that matters:")
    print("an agent resends a stable prefix every turn and pays almost nothing for it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
