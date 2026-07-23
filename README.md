# llm-coding-bench

LLM coding / architecture benches for local [Ollama](https://ollama.com) models **and** [Cursor Agent CLI](https://cursor.com/docs/cli/overview) cloud models.

Suites:

| Suite | Entry | What it measures |
|---|---|---|
| **Ruby hard** | `hard_bench.rb` | Harder Ruby coding tasks (MoE stress) |
| **Pyhard** | `hard_bench_py.py` | 9 Python tasks / 99 pts (regex, LRU, alien dict, expr eval, VM fix, SAT, JSON patch, unify, mini SQL) |
| **Archbench** | `archbench/arch_bench.py` | Tools-first exploration of a planted buggy shop API (9 tasks / 90 pts) |
| **Claim probe** | `archbench/claim_bench.py` | 15 true/false traps over the same fixture (tie-break) |

## Requirements

**Ollama backend**

- [Ollama](https://ollama.com) with models pulled locally
- Network to `localhost:11434`

**Cursor backend**

- Cursor Agent CLI (`agent`) — `curl https://cursor.com/install -fsS | bash`
- Authenticated account — `agent login` / `agent status`
- Model IDs from `agent models` or `./list_cursor_models.sh`

**Shared**

- Python **3.14+** (pyhard / archbench graders)
- Ruby (Ruby benches only)

Results live under `results/` (override with `BENCH_OUT`).

## Quick start (Ollama)

```bash
export BENCH_MODEL='qwen3-coder:30b-a3b-fp16'
export BENCH_TAG='30b_pyhard'
python3.14 hard_bench_py.py

cd archbench
export BENCH_MODEL='qwen3-coder-next:q8_0'
python3.14 arch_bench.py
```

## Cursor Agent CLI backend

Set `BENCH_PROVIDER=cursor` and a Cursor model id (`composer-2.5`, `gpt-5.4-mini-medium`, …).

```bash
# list models for your account
./list_cursor_models.sh

# pyhard via Cursor ask-mode (isolated empty workspace)
./run_cursor_pyhard.sh composer-2.5

# archbench via Cursor ask-mode (workspace = archbench/fixture/shopapi)
./run_cursor_arch.sh composer-2.5

# or manually
BENCH_PROVIDER=cursor BENCH_MODEL='composer-2.5' python3.14 hard_bench_py.py
BENCH_PROVIDER=cursor BENCH_MODEL='composer-2.5' \
  BENCH_TASKS=tenant_invoice_isolation python3.14 archbench/arch_bench.py
```

How it works:

- Invokes `agent -p --output-format json --mode ask --model <id> --trust`
- Prompt is passed on stdin (avoids ARG_MAX issues)
- Pyhard uses a throwaway empty `--workspace` so the agent cannot edit this repo
- Archbench points `--workspace` at `archbench/fixture/shopapi` and swaps the Ollama `<arch_tool>` preamble for Cursor-native tool instructions
- Usage tokens come from the CLI JSON `usage` object when present

Useful Cursor env knobs:

| Env | Default | Meaning |
|---|---|---|
| `BENCH_PROVIDER` | `ollama` | `ollama` or `cursor` |
| `BENCH_CURSOR_MODE` | `ask` | CLI `--mode` (`ask` / `plan`) |
| `BENCH_CURSOR_TIMEOUT` | `1800` | seconds per invocation |
| `BENCH_CURSOR_FORCE` | `0` | pass `--force` / yolo |
| `CURSOR_AGENT_BIN` | (`agent` on PATH) | override binary path |

## Self-tests

```bash
BENCH_SELFTEST=1 python3.14 hard_bench_py.py
BENCH_SELFTEST=1 python3.14 archbench/arch_bench.py
BENCH_SELFTEST=1 python3.14 archbench/claim_bench.py
python3.14 bench_lib/test_cursor_cli.py
```

Other knobs: `BENCH_NUM_CTX`, `BENCH_NUM_PREDICT`, `BENCH_TEMPERATURE`, `BENCH_TASKS`, `BENCH_THINK`, `BENCH_OUT`, `BENCH_TAG`.

## Published compare notes (Ollama, Jul 2026)

- `results/compare_hard_64k.md` — Ruby hard
- `results/compare_pyhard_64k.md` — Pyhard @16k predict
- `results/compare_pyhard_hibudget.md` — Pyhard @49k
- `results/compare_pyhard_rerun.md` — Next/30B re-run
- `results/pyhard_failure_autopsy.md` — partial-credit rescoring
- `results/archbench/compare_archbench.md` — arch + claim leaderboard

## Notes

- Archbench’s Ollama tool protocol uses `<arch_tool>` / `<arch_final>` (not `<tool_call>`) so Qwen tool parsers do not EOF the server.
- Older `run_*.sh` wrappers may still mention `$HOME/.ollama/bench`; prefer repo-relative entrypoints / `BENCH_OUT`.
