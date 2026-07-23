# llm-coding-bench

Local LLM coding / architecture benches for [Ollama](https://ollama.com) models.

Suites:

| Suite | Entry | What it measures |
|---|---|---|
| **Ruby hard** | `hard_bench.rb` | Harder Ruby coding tasks (MoE stress) |
| **Pyhard** | `hard_bench_py.py` | 9 Python tasks / 99 pts (regex, LRU, alien dict, expr eval, VM fix, SAT, JSON patch, unify, mini SQL) |
| **Archbench** | `archbench/arch_bench.py` | Tools-first exploration of a planted buggy shop API (9 tasks / 90 pts) |
| **Claim probe** | `archbench/claim_bench.py` | 15 true/false traps over the same fixture (tie-break) |

## Requirements

- [Ollama](https://ollama.com) with models pulled locally
- Python **3.14+** (used for pyhard / archbench graders)
- Ruby (for Ruby benches)
- `curl` / network to `localhost:11434`

Results from the M5 Max laptop runs (Jul 2026) live under `results/` (compare markdown + `*_latest.json` summaries).

## Quick start

```bash
# Pyhard (default num_ctx/num_predict via env)
export BENCH_MODEL='qwen3-coder:30b-a3b-fp16'
export BENCH_TAG='30b_pyhard'
python3.14 hard_bench_py.py

# Archbench
cd archbench
export BENCH_MODEL='qwen3-coder-next:q8_0'
export BENCH_TAG='next_arch'
python3.14 arch_bench.py

# Claim probe (after arch, for ties)
export BENCH_MODEL='qwen3-coder-next:q8_0'
export BENCH_TAG='next_claim'
python3.14 claim_bench.py

# Self-tests (no model)
BENCH_SELFTEST=1 python3.14 hard_bench_py.py
BENCH_SELFTEST=1 python3.14 archbench/arch_bench.py
BENCH_SELFTEST=1 python3.14 archbench/claim_bench.py
```

Useful env knobs: `BENCH_NUM_CTX`, `BENCH_NUM_PREDICT`, `BENCH_TEMPERATURE`, `BENCH_TASKS`, `BENCH_THINK`.

## Published compare notes

- `results/compare_hard_64k.md` — Ruby hard
- `results/compare_pyhard_64k.md` — Pyhard @16k predict
- `results/compare_pyhard_hibudget.md` — Pyhard @49k
- `results/compare_pyhard_rerun.md` — Next/30B re-run
- `results/pyhard_failure_autopsy.md` — partial-credit rescoring
- `results/archbench/compare_archbench.md` — arch + claim leaderboard

## Notes

- Archbench tool protocol uses `<arch_tool>` / `<arch_final>` tags (not `<tool_call>`) so Qwen tool parsers do not EOF the server.
- LaunchAgent / absolute-path wrapper scripts under `run_*.sh` assume macOS + `$HOME/.ollama/bench` historically; point them at this repo or run the Python/Ruby entrypoints directly.
