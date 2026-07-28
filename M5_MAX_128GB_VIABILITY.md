# Local coding models on a MacBook Pro M5 Max / 128 GB — viability analysis

_Compiled 2026-07-28. Every figure below is attributed; vendor self-claims are labelled as
such. Read the [Source credibility](#source-credibility-read-this-first) section before
trusting any ranking you find on the open web for this machine._

## TL;DR

- **Daily driver: `Qwen3.6-35B-A3B` (Q8, llama.cpp, MTP on).** ~105 tok/s decode, ~45 GB
  resident, Apache 2.0. It is the only model in the viable set that is simultaneously fast,
  comfortably resident, and current.
- **When you need the better coder and can eat the latency: `Qwen3.6-27B` (dense, Q8).**
  ~32 tok/s with MTP, ~42 GB. Higher SWE-bench Verified than the MoE (77.2 vs 73.4, both
  self-claimed) at roughly a third of the throughput.
- **Ceiling experiment: `DeepSeek-V4-Flash` at 2-bit via `ds4`.** ~33 tok/s, ~103 GB
  resident — it fits, but it consumes the machine and the 2-bit quant is not the model whose
  benchmark scores you read.
- **`GLM-5.2` does not fit.** It is 744B-A40B (~1.51 TB BF16), not the "106B Air" some
  listicles claim. Even 2-bit lands around 186 GB.
- **Use llama.cpp, not MLX.** On this chip llama.cpp measured 10–24 % faster than MLX on the
  same models — the opposite of the usual Apple-Silicon folklore.

## Source credibility (read this first)

This analysis was kicked off from
[llmcheck.net/best-llm/macbook-pro-m5-max-128gb](https://llmcheck.net/best-llm/macbook-pro-m5-max-128gb/).
**That page should not be used.** Findings:

1. **Four of its top-twelve models do not exist.** It ranks `Qwen 4.1 32B-A3B` (#1),
   `Qwen 4` (#2), `Qwen 4 Coder` (#3) and `Qwen 4 Preview 32B-A3B` (#4). A Hugging Face API
   search for `Qwen4` returns nothing but unrelated 2024–2025 hobbyist repos, and the
   official [QwenLM release log](https://github.com/QwenLM/Qwen3.5) ends at **Qwen3.6-27B on
   2026-04-22**. There is no Qwen 4 series. Its companion
   ["Qwen 4 Coder review"](https://llmcheck.net/blog/qwen-4-coder-review/) invents a
   2026-06-02 release date and an 82 % SWE-Verified score.
2. **`GLM 5.2 Air, 106B` is fabricated.** The real
   [GLM-5.2](https://z.ai/blog/glm-5.2) (2026-06-16, MIT) is 744B-A40B; the official model
   table lists no "Air" variant.
3. **Its speeds are not measurements.** The page states they are "index estimates
   (memory-bandwidth model)". Where a real measurement exists they are off by ~2×: it puts
   `Qwen 3.6-35B-A3B` at 48 tok/s; measured on this exact machine it is 93–105 tok/s.
4. **`Qwen3-235B-A22B` "fits in 128 GB"** is technically true only at Q3-and-below. Q4_K_M
   is ~133 GB of weights before any KV cache. It is also an April-2025 model being sold as a
   2026 recommendation.
5. The site is Amazon-affiliate monetised, which rewards page coverage per hardware SKU
   rather than accuracy.

Its `Gemma 4 31B` / `Gemma 4 26B-A4B` entries do correspond to real models. `Llama 5 70B`,
`Phi-5 Large 28B` and `Mistral Medium 4` were **not verified either way** — absence of
confirmation here is not proof they are fake.

**The one high-quality source for this exact machine** is
[stared/benching-local-llms-on-apple-silicon](https://github.com/stared/benching-local-llms-on-apple-silicon)
(measured 2026-06-14, M5 Max 128 GB, apples-to-apples harness, median of 3 runs). Caveat:
single author, single machine, n=3. It is still an order of magnitude better evidence than
anything else available.

## The hardware envelope

| Property | Value | Source |
|---|---|---|
| Chip variant carrying 128 GB | M5 Max, 18-core CPU / **40-core GPU only** | [Apple tech specs](https://support.apple.com/en-us/126319) |
| Memory bandwidth | **614 GB/s** (512-bit bus, 8× LPDDR5X @ 9600 MT/s) | Apple specs / [Apple newsroom](https://www.apple.com/mz/newsroom/2026/03/apple-introduces-macbook-pro-with-all-new-m5-pro-and-m5-max/) |
| 32-core GPU M5 Max | 460 GB/s, caps at 36 GB — irrelevant here | Apple tech specs |
| Prompt processing | Apple claims up to **4× faster LLM prefill than M4 Max**, via a Neural Accelerator in each GPU core | Apple newsroom (vendor claim, Jan–Feb 2026 testing) |

Two consequences that drive everything below:

- **Decode is bandwidth-bound.** 614 GB/s over an *N*-GB resident model caps you near
  `614/N` tok/s. A 45 GB model tops out around 13 tok/s per pass — which is why MoE models
  with ~3B active parameters win so decisively: they only stream the active experts.
- **Prefill is compute-bound, and this is the generation where that got fixed.** For agentic
  coding — where you shove 20k+ tokens of repository context in before a single token comes
  out — the M5's per-core neural accelerators matter more than the decode number everyone
  quotes. No independent M5 Max prefill benchmark was found; treat Apple's 4× as a vendor
  claim.

Budget **~96–112 GB usable** in practice: macOS reserves memory, `iogpu.wired_limit_mb`
gates GPU-wired allocations, and you presumably want an IDE and a browser alive.

## Viability by model

Memory figures are measured resident (weights + KV at the stated context) where the source
measured them, and computed weight-only estimates where marked ~.

| Model | Params | License | Fits 128 GB? | Decode (measured) | Verdict |
|---|---|---|---|---|---|
| **Qwen3.6-35B-A3B** (MoE) | 35B / A3B | Apache 2.0 | Yes, 45 GB @ Q8 | **105** tok/s (MTP), 97 @ 8k | **Daily driver** |
| **Qwen3.6-27B** (dense) | 27B | Apache 2.0 | Yes, 42 GB @ Q8 | 32 tok/s (MTP), 18 without | **Best coder that stays comfortable** |
| **DeepSeek-V4-Flash** | 284B / A13B | MIT | Barely — 103 GB @ ~2-bit | 33 tok/s, 28 @ 8k | Ceiling experiment, not a daily driver |
| Gemma 4 26B-A4B (MoE) | 26B / ~A4B | Apache 2.0 | Yes, ~26 GB @ Q8 | Not measured on M5 Max | Plausible fast option, **unbenchmarked here** |
| Gemma 4 31B (dense) | 31B | Apache 2.0 | Yes, ~31 GB @ Q8 | Not measured on M5 Max | Strong general model, weaker agentic-coding evidence |
| Qwen3-Coder-30B-A3B | 30B / A3B | Apache 2.0 | Yes, ~30 GB @ Q8 | Not measured on M5 Max | Older (2025) but genuinely coder-tuned |
| **GLM-5.2** | 744B / A40B | MIT | **No** — ~186 GB at 2-bit | — | Excluded. Buy the API instead |
| DeepSeek-V4-Pro | 1.6T / A49B | MIT | **No** (SSD-streaming only, "experimental") | — | Excluded |
| Qwen3-235B-A22B | 235B / A22B | Apache 2.0 | Only ≤Q3 | — | Excluded: April-2025 model, no headroom |

### Qwen3.6-35B-A3B — the default

Released [2026-04-16](https://github.com/QwenLM/Qwen3.5), Apache 2.0. Measured 105 tok/s at
128-token context and **97 tok/s at 8k** — the barely-degrading long-context behaviour is the
real selling point for agent loops, more than the peak number.

Self-claimed capability: SWE-bench Verified 73.4, SWE-bench Pro 49.5 (on Qwen's own refined
set, ~+11 vs Scale's public set), GPQA Diamond 86.0. Independent cross-check via Artificial
Analysis composite: **33**. That places it around the cloud frontier of *early-to-mid 2025* —
roughly Claude 4 Sonnet territory on SWE-V.

MTP (speculative decoding) gives only **+12 %** here, because a 3B-active MoE is already
compute-light. Download the MTP GGUF anyway: it is a superset and runs fine without the flag.

### Qwen3.6-27B — the better coder

Released 2026-04-22, Apache 2.0, dense. Alibaba's
[own write-up](https://www.alibabacloud.com/blog/qwen3-6-27b-flagship-level-coding-in-a-27b-dense-model_603063)
claims it beats the previous 397B-A17B flagship on every major coding benchmark: SWE-bench
Verified 77.2, SWE-bench Pro 53.5, Terminal-Bench 2.0 59.3, GPQA Diamond 87.8. Artificial
Analysis composite: **37** — higher than the 35B-A3B, consistent with the self-claims.

The cost is brutal and structural: dense 27B means every parameter streams every token.
18 tok/s baseline, 32 with MTP (**+75 %** — dense models have far more headroom for
speculation). For interactive agent work that is the difference between "thinking" and
"stalled".

Note both Qwen3.6 models are **multimodal**, which the coding-focused listicles omit.

### DeepSeek-V4-Flash — the interesting failure mode

284B total / 13B active, MIT, 1M native context,
[arXiv 2606.19348](https://arxiv.org/html/2606.19348). On paper the strongest thing that
touches this machine: SWE-V 78.6, SWE-Pro 52.3, LiveCodeBench v6 88.4, AA composite **46**.

The catch is that **none of those numbers describe what you can run.** Getting it onto 128 GB
requires antirez's [`ds4`](https://github.com/antirez/ds4) engine and a purpose-built ~2-bit
quant — [80.8 GiB](https://huggingface.co/antirez/deepseek-v4-gguf), measured at 103 GB
resident. Specifically:

- `ds4` is **not a general GGUF runner**; only the quants published for it will load.
- The full 1M context needs ~26 GB on top of the weights (the compressed indexer alone is
  ~22 GB). On 128 GB you realistically cap at **100–300k tokens**. You bought a 1M-context
  model and can use a quarter of it.
- The stared benchmark explicitly flags that the 91 GB 2–4-bit quant **scores lower** than the
  full-precision figures quoted above. Nobody has published how much lower.
- It leaves ~25 GB for the entire rest of the machine.

Worth benchmarking precisely because the quantisation delta is unmeasured. Not worth
defaulting to.

### GLM-5.2 — excluded, and why the listicles get it wrong

744B-A40B, MIT, 1M context, released 2026-06-16. Simon Willison
[measured the download at 1.51 TB](https://simonwillison.net/2026/jun/17/glm-52/). Even an
aggressive 2-bit quant is ~186 GB; 1.5-bit is ~140 GB. Neither fits. There is no official
"Air" variant. If you want GLM-5.2, use the API — it is ~$1.40/M in, $4.40/M out.

## Engine and configuration

Runner choice — including whether Ollama's flak is deserved — is analysed separately in
[`RUNNERS_MACOS_METAL.md`](RUNNERS_MACOS_METAL.md). Two measured results worth overriding your
priors on:

1. **llama.cpp beat MLX by 10–24 %** on these models on the M5 Max. The "MLX is faster on
   Apple Silicon" claim is not true here. MLX does use less memory (37 GB vs 45 GB for the
   35B-A3B), so it remains the pick if you are memory-squeezed.
2. **MTP speculative decoding is lossless and helps unevenly** — +75 % on the dense 27B,
   +12 % on the MoE. Always download the MTP build; it is a superset.

```bash
hf download unsloth/Qwen3.6-35B-A3B-MTP-GGUF Qwen3.6-35B-A3B-Q8_0.gguf --local-dir ~/models

llama-server -m ~/models/Qwen3.6-35B-A3B-Q8_0.gguf \
  --spec-type draft-mtp --spec-draft-n-max 3 \
  -ngl 999 -fa on -c 65536 --parallel 1 --jinja \
  --host 127.0.0.1 --port 8080
```

Q8 throughout, because at 128 GB you can afford it — the usual Q4 quality tax buys headroom
you do not need for a 35B model.

## Honest capability positioning

The best thing you can run locally on this laptop sits roughly at the **cloud frontier of
mid-2025 to early-2026**. The live frontier is not close: HLE 53.3 (Fable 5, Jun '26) and
SWE-bench Pro 59.1 (GPT-5.4) versus ~53.5 for the best local option on the latter, and that is
a cross-scaffold comparison on a vendor-refined set.

So the case for local is **not** capability. It is privacy, zero marginal cost on
token-hungry agent loops, offline operation, and no rate limits. If you are choosing local
because you think you get frontier quality, you are buying the wrong thing.

Two further honesty caveats:

- Every capability number above except the Artificial Analysis composites is a **vendor
  self-claim**, produced on the vendor's own scaffold. Cross-scaffold comparisons are worth
  ±several points, not decimal places.
- SWE-bench Verified is saturating and correlates imperfectly with the repo-scale, multi-file,
  long-horizon work these models are actually being asked to do. That is the gap this
  repository's `repohard` and `audittrap` phases exist to probe.

## Follow-up work for this repo

The published numbers do not answer the questions that matter for the benches here:

1. Run `pyhard` / `arch` / `repohard` against `Qwen3.6-35B-A3B` and `Qwen3.6-27B` at Q8 — the
   35B's throughput advantage may not survive contact with tool-use tasks that are prefill-
   heavy rather than decode-heavy.
2. **Measure the DeepSeek-V4-Flash 2-bit quality delta.** Nobody has published it. A `ds4`
   provider in `bench_lib` against the same suites would be a genuinely novel data point.
3. Benchmark **prefill**, not just decode. Apple's 4× prefill claim is unverified by any
   third party, and for `arch`/`repohard` — which front-load large fixture context — prefill
   dominates wall-clock.
4. Add Gemma 4 26B-A4B and 31B. Both are real, Apache 2.0, and completely unmeasured on this
   chip.

## Sources

| Source | Date | Authority | Used for |
|---|---|---|---|
| [Apple MacBook Pro M5 Max tech specs](https://support.apple.com/en-us/126319) | 2026 | Official | Bandwidth, memory configs |
| [Apple newsroom, M5 Pro/Max](https://www.apple.com/mz/newsroom/2026/03/apple-introduces-macbook-pro-with-all-new-m5-pro-and-m5-max/) | Mar 2026 | Official (marketing) | Prefill claim |
| [stared/benching-local-llms-on-apple-silicon](https://github.com/stared/benching-local-llms-on-apple-silicon) | 2026-06-14 | Independent, measured, n=1 machine | All M5 Max throughput figures |
| [QwenLM/Qwen3.6 repo](https://github.com/QwenLM/Qwen3.5) | 2026-04-22 | Official | Release log; disproving "Qwen 4" |
| [Qwen3.6-27B announcement](https://www.alibabacloud.com/blog/qwen3-6-27b-flagship-level-coding-in-a-27b-dense-model_603063) | 2026-04-24 | Official (self-claim) | 27B benchmarks |
| [DeepSeek-V4 paper](https://arxiv.org/html/2606.19348) | Jun 2026 | Official | Architecture, params |
| [antirez/ds4](https://github.com/antirez/ds4) + [GGUF repo](https://huggingface.co/antirez/deepseek-v4-gguf) | 2026 | Independent, practitioner | 128 GB DeepSeek path, quant sizes |
| [z.ai GLM-5.2](https://z.ai/blog/glm-5.2), [zai-org/GLM-5](https://github.com/zai-org/GLM-5) | 2026-06-16 | Official | GLM-5.2 size; disproving "GLM 5.2 Air" |
| [Gemma 4 announcement](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/) | 2026-04-02 | Official | Gemma 4 lineup, license |
| [llmcheck.net M5 Max page](https://llmcheck.net/best-llm/macbook-pro-m5-max-128gb/) | 2026 | **Low — affiliate SEO, fabricated models** | Cited only as the thing being refuted |

**Confidence:** high on hardware specs and on which models exist and fit; high on the M5 Max
throughput numbers but from a single independent measurement; **moderate** on all capability
rankings, since they rest largely on vendor self-claims across inconsistent scaffolds.
