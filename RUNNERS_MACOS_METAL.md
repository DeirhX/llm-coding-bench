# Inference runners on macOS / Metal — which one, and is the Ollama flak deserved?

_Compiled 2026-07-28. Companion to [`M5_MAX_128GB_VIABILITY.md`](M5_MAX_128GB_VIABILITY.md),
which covers **which models** fit an M5 Max 128 GB. This document covers **what to run them
with**. Claims are tagged by evidence class: **[primary]** vendor docs/source, **[measured]**
independent benchmark, **[vendor]** self-published marketing, **[advocacy]** opinion posts._

## TL;DR

- **Run `llama-server` (llama.cpp) directly.** It is the only runner with an independent,
  reproducible measurement on this exact hardware, and it won there.
- **Ollama's MLX engine will almost certainly never touch your models.** It is
  **safetensors-only**; every GGUF you own still runs through llama.cpp. This is the single
  most misreported fact in the 2026 Mac-inference writeups.
- **The Ollama criticism is substantially true on governance** (attribution, lock-in,
  closed-source GUI, cloud pivot) and **plausible but poorly evidenced on performance**. Most
  of the "flak" traces back to one widely-syndicated post.
- **For this repo specifically** the risk is not speed, it is **silent engine heterogeneity**:
  Ollama can run two models in the same leaderboard on two different engines without telling
  you. Record the engine per run.
- **MLX is not the automatic Mac winner.** The folklore says +20–40 %; the one measurement on
  an M5 Max says llama.cpp wins by 10–24 %.

## The decision

| You want | Use | Why |
|---|---|---|
| Reproducible benchmarking on Apple Silicon | **llama.cpp (`llama-server`)** | Explicit flags, no hidden defaults, measured fastest on M5 Max, MIT |
| Lowest memory footprint for the same model | **MLX / `mlx-lm`** | 37 GB vs 45 GB for Qwen3.6-35B-A3B **[measured]** |
| Fine-tuning / LoRA on-device | **MLX** | llama.cpp is inference-only |
| Convenience, model management, mixed team | **Ollama** — with eyes open | Real ergonomics, real governance baggage |
| GUI, backend toggling, model discovery | **LM Studio** | Closed-source binary; fine for exploration, poor provenance |
| DeepSeek-V4-Flash on 128 GB | **`ds4`** | Only engine that loads the 2-bit build; not a general GGUF runner |
| Multi-user production serving | **not a Mac** | vLLM's Metal path is still immature |

## The thing everyone gets wrong: Ollama's engine routing

Since **v0.30.0 (2026-05-13)** Ollama runs a **dual-engine architecture**, and the engine is
chosen **by model file format, not by a setting**:

- **GGUF → llama.cpp** (spawned as a `llama-server` subprocess)
- **safetensors → MLX** (in-process, macOS arm64 only)

Ollama's own development docs state it plainly **[primary]**:

> The MLX engine enables running safetensor based models. On macOS arm64, MLX is enabled by
> default.

There is **no `OLLAMA_USE_MLX` toggle** in shipped versions. So:

- Every GGUF you have already pulled gets **zero** MLX benefit.
- Articles claiming things like "Ollama 0.19 gets 82 tok/s on Llama 70B **Q4**" are describing
  an MLX speedup on a quantisation that, being GGUF, cannot use MLX. Treat any runner
  comparison that makes this mistake as unreliable in full.
- The MLX preview shipped supporting **exactly one model** (Qwen3.5-35B-A3B, NVFP4) and has
  broadened since, but format-gating remains the rule.

This also means **Ollama did not "abandon" or "revert" llama.cpp** — both engines ship and both
are bumped nearly every release. The v0.32.3 changelog even shows Ollama *deleting* its local
Laguna implementation to
[realign with upstream llama.cpp](https://github.com/ollama/ollama/pull/17335) **[primary]**,
which is a genuine reversal of the fork-away strategy and deserves acknowledgement.

## Performance: the numbers disagree, and the disagreement is informative

Three sources, three answers:

| Source | Claim | Class |
|---|---|---|
| [stared, M5 Max 128 GB, 2026-06-14](https://github.com/stared/benching-local-llms-on-apple-silicon) | **llama.cpp beats MLX by 10–24 %** (Qwen3.6 Q8 / MLX 8-bit) | **[measured]** |
| Ollama 0.19 launch | MLX vs their llama.cpp path: decode **58 → 112 tok/s**, prefill 1,154 → 1,810, on M5 Max / Qwen3.5-35B-A3B NVFP4 | **[vendor]** |
| Assorted 2026 roundups | "MLX is 20–40 % faster" | **[advocacy]**, mostly extrapolated from M2-era 8B tests |

These are less contradictory than they look — the quantisation formats differ (GGUF Q8 vs
NVFP4 safetensors), and NVFP4 moves far fewer bytes per token, which on a bandwidth-bound
machine is most of the story. The comparison is confounded, and Ollama chose the confound.

**But look at Ollama's baseline.** Their llama.cpp path produced **58 tok/s** on a 35B-A3B MoE
on an M5 Max. Independently, llama.cpp driven directly produced **93 tok/s without MTP and 105
with** on the same class of model on the same chip — at **Q8**, which streams *more* bytes per
token than NVFP4 and should therefore be *slower*.

That is roughly a **40 % gap in the wrong direction**, and it admits two readings:

1. Ollama's llama.cpp integration leaves substantial performance on the floor (no MTP,
   conservative defaults, subprocess overhead), which is exactly what the critics allege; or
2. Ollama benchmarked an unconfigured baseline against a tuned contender, which is ordinary
   marketing.

Neither reading is flattering, and both point the same direction: **drive llama.cpp yourself**.
Note this is inference from two sources that did not run the same experiment — it is
suggestive, not proof. Nobody has published a controlled Ollama-vs-`llama-server` comparison
on an M5 Max, which is a gap this repo could fill.

## Is the Ollama flak deserved?

Sorting the accusations by how well they survive contact with primary sources.

### Substantiated

- **Attribution failure.** llama.cpp is MIT; MIT has essentially one obligation — ship the
  copyright notice. Ollama went 400+ days with issue #3185 open, no README credit, and no
  license notice in binary distributions. This is not etiquette, it is the licence text.
- **Format lock-in.** Models are stored as **hashed blobs**, so you cannot point LM Studio or
  `llama-server` at the same file. `Modelfile` reintroduces external config to GGUF, whose
  entire design goal was a single self-describing file — and changing one parameter can copy
  tens of GB.
- **Closed-source GUI shipped under an open-source banner**, initially from a private repo with
  unclear licensing, with the download button next to the GitHub link.
- **Misleading model naming.** `ollama run deepseek-r1` historically pulled an 8B distill of
  Qwen or Llama, not the 671B model. For a *benchmarking* repo this class of bug is
  disqualifying on its own — you cannot publish a leaderboard if the runner lies about which
  weights it loaded.
- **Cloud pivot in a local-first product.** VC-backed, with hosted models appearing in the
  model list; several critics report insufficient disclosure that prompts leave the machine.

### Weakly evidenced

- **"llama.cpp is 1.8× faster" / "30–70 % throughput penalty."** These specific figures
  circulate without a reproducible harness attached. The direction is corroborated by the
  58-vs-93 discrepancy above, but the magnitudes should not be quoted as fact.
- **The volume of criticism is misleading.** At least four of the posts surfaced are the same
  argument — several literally share the title "Friends Don't Let Friends Use Ollama" — largely
  tracing to one author (zetaphor). One well-argued post syndicated widely is not five
  independent confirmations. The underlying licensing facts are checkable and hold up; the
  chorus is smaller than it sounds.

### Outdated or wrong

- **"Ollama abandoned llama.cpp."** False as of mid-2026. Dual engine, and actively
  re-converging with upstream.
- **"Ollama's custom backend is all you get."** The bad-fork era is real history, but the
  GGUF path today is upstream llama.cpp, bumped near-continuously.
- **"Ollama's default context silently truncates your prompts."** Still the most common real
  footgun in the wild — **but not in this repo**, see below.

**Verdict:** the governance criticism is deserved and mostly unremediated. The performance
criticism is directionally supported but quantitatively sloppy. Ollama in mid-2026 is
technically much better than its worst period and ethically about the same.

## What this means for `llm-coding-bench`

The harness is already correct on the classic hazard: every bench pins
`num_ctx = int(os.environ.get("BENCH_NUM_CTX", "65536"))` — `benches/{pyhard,arch,claim,repohard,audittrap}/bench.py`
all set it, and the run scripts export it explicitly. Ollama's low default context cannot
silently truncate these runs.

Four risks that are **not** yet handled:

1. **Silent engine heterogeneity — the serious one.** Because routing keys off file format, a
   GGUF model and a safetensors model in the same leaderboard run on **different engines**. Any
   throughput or latency column then conflates model quality with engine choice. Since grading
   is correctness-based this does not directly corrupt scores — except through
   `BENCH_TASK_TIMEOUT_S`, where a slower engine turns into truncated work and a genuinely
   lower score. **Record the engine and quant per run in the result JSON.**
2. **Version drift.** Ollama bumps its bundled llama.cpp nearly every release, so a re-run
   three weeks later is not the same experiment. **Record `ollama --version` alongside results.**
3. **Weight provenance.** Hashed blobs plus `Modelfile` make it hard to prove which weights
   produced a number. **Record the model digest**, not just the tag.
4. **The performance tax is unmeasured here.** Nobody has published Ollama vs raw
   `llama-server` on an M5 Max. Adding a `llama-server` provider next to the existing
   `ollama` and `cursor` providers in `bench_lib/` would let this repo answer that
   question — and would remove the wrapper from the measurement path entirely.

Concretely: an `llama_server.py` provider hitting `http://127.0.0.1:8080/v1` needs little more
than the existing `cursor_cli.py`-style shim, since `llama-server` speaks the OpenAI API. That
also unlocks MTP, which Ollama does not expose per-request and which is worth **+75 %** on
dense models.

## Recommended setup

```bash
# Benchmarking / anything you intend to publish a number about
llama-server -m ~/models/Qwen3.6-35B-A3B-Q8_0.gguf \
  --spec-type draft-mtp --spec-draft-n-max 3 \
  -ngl 999 -fa on -c 65536 --parallel 1 --jinja \
  --host 127.0.0.1 --port 8080
```

Keep Ollama if you like it for casual use — it is genuinely the least friction — but do not put
it between yourself and a number you plan to publish. If you want MLX's lower memory footprint,
use `mlx-lm` directly rather than hoping Ollama routes you there.

## Sources

| Source | Date | Authority | Used for |
|---|---|---|---|
| [Ollama development docs](https://github.com/ollama/ollama/blob/main/docs/development.md) | current | **Primary** | MLX = safetensors-only, default on macOS arm64 |
| [ollama/ollama#17335](https://github.com/ollama/ollama/pull/17335) | 2026-07-23 | **Primary** | Re-alignment with upstream llama.cpp |
| [Ollama changelog](https://www.change8.dev/package/ollama) | May–Jul 2026 | Secondary, derived from releases | Dual-engine timeline, MTP work, llama.cpp bump cadence |
| [stared/benching-local-llms-on-apple-silicon](https://github.com/stared/benching-local-llms-on-apple-silicon) | 2026-06-14 | Independent, **measured**, n=1 machine | llama.cpp vs MLX on M5 Max; MTP deltas |
| [Ollama 0.19 MLX writeups](https://levelup.gitconnected.com/ollama-mlx-nearly-doubles-llm-speed-on-your-mac-58-to-112-tok-s-and-your-old-models-get-none-of-fd1a9ec531fb) | Mar 2026 | Secondary reporting **[vendor]** figures | 58 → 112 tok/s claim; format-gating confirmation |
| [zetaphor critique](https://sleepingrobots.com/dreams/stop-using-ollama/) and syndications | 2026 | **[advocacy]**, single origin | Attribution timeline, lock-in mechanics |
| [xda-developers on Ollama](https://www.xda-developers.com/ollama-easiest-way-start-local-llms-worst-keep-running/) | 2026 | Trade press, more balanced | Corroborates licensing and GUI complaints |

**Confidence:** high on engine routing and licensing history (both from primary sources);
high on the llama.cpp-vs-MLX result for this chip but from a single independent measurement;
**low** on the specific magnitude of any Ollama performance tax — that number does not yet
exist in a form worth citing, which is precisely why it is listed as follow-up work above.
