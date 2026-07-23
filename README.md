# llm-coding-bench

LLM coding / architecture benches for local [Ollama](https://ollama.com) models **and** [Cursor Agent CLI](https://cursor.com/docs/cli/overview) cloud models.

## Layout

```
benches/
  shopapi/            # SHARED fixture (arch + claim)
    fixture/shopapi/  # code under test / Cursor workspace
    tools.py          # ToolSession sandbox
    MAINTAINER_NOTES.md
  pyhard/
    assignment/*.md   # ← TASK PROMPTS
    bench.py
  arch/
    assignment/*.md   # ← TASK PROMPTS
    preamble.md
    tasks.py          # graders + gold trajectories
    bench.py
  claim/
    claims.yaml       # ← T/F ASSIGNMENT
    bench.py
  registry.py
bench_lib/
scripts/
results/
run.py
```

| Phase | ID | Assignment lives in | What it measures |
|---|---|---|---|
| **Pyhard** | `pyhard` | `benches/pyhard/assignment/*.md` | 9 Python tasks / 99 pts |
| **Archbench** | `arch` | `benches/arch/assignment/*.md` + shared shopapi | Tools-first exploration (9 / 90) |
| **Claim probe** | `claim` | `benches/claim/claims.yaml` + shared shopapi | 20 true/false traps (tie-break) |

Graders stay in Python (they must execute). Prompts/claims are data.

### Adding a new bench

1. Create `benches/<id>/` with `assignment/` (or equivalent data) + `bench.py` exposing `main()`
2. Add `__main__.py` and register a `BenchSpec` in `benches/registry.py`
3. Optionally teach reporting about its `*_latest.json` suffix

## Requirements

**Ollama backend**

- [Ollama](https://ollama.com) with models pulled locally
- Network to `localhost:11434`

**Cursor backend**

- Cursor Agent CLI (`agent`) — `curl https://cursor.com/install -fsS | bash`
- Authenticated account — `agent login` / `agent status`
- Model IDs from `agent models` or `./scripts/list_cursor_models.sh`

**Shared**

- Python **3.14+** (pyhard / archbench graders)

Results live under `results/` (override with `BENCH_OUT`).

## Quick start

```bash
# list phases
python3.14 run.py list

# Ollama pyhard
export BENCH_MODEL='qwen3-coder:30b-a3b-fp16'
export BENCH_TAG='30b_pyhard'
python3.14 run.py run pyhard

# Ollama arch
export BENCH_MODEL='qwen3-coder-next:q8_0'
python3.14 run.py run arch

# all registered phases
python3.14 run.py run all

# leaderboard from results/*_latest.json → stdout + results/REPORT.md
python3.14 run.py report
```

Or per-phase modules:

```bash
python3.14 -m benches.pyhard
python3.14 -m benches.arch
python3.14 -m benches.claim
```

## Cursor Agent CLI backend

Set `BENCH_PROVIDER=cursor` and a Cursor model id (`composer-2.5`, `gpt-5.4-mini-medium`, …).

```bash
./scripts/list_cursor_models.sh
./scripts/run_cursor_pyhard.sh composer-2.5
./scripts/run_cursor_arch.sh composer-2.5

BENCH_PROVIDER=cursor BENCH_MODEL='composer-2.5' python3.14 run.py run pyhard
BENCH_PROVIDER=cursor BENCH_MODEL='composer-2.5' \
  BENCH_TASKS=tenant_invoice_isolation python3.14 run.py run arch
```

How it works:

- Invokes `agent -p --output-format json --mode ask --model <id> --trust`
- Prompt is passed on stdin (avoids ARG_MAX issues)
- Pyhard uses a throwaway empty `--workspace` so the agent cannot edit this repo
- Arch/claim point `--workspace` at `benches/shopapi/fixture/shopapi` and swap the Ollama `<arch_tool>` preamble for Cursor-native tool instructions
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
python3.14 run.py selftest
python3.14 run.py selftest pyhard
python3.14 bench_lib/test_cursor_cli.py
```

Other knobs: `BENCH_NUM_CTX`, `BENCH_NUM_PREDICT`, `BENCH_TEMPERATURE`, `BENCH_TASKS`, `BENCH_THINK`, `BENCH_OUT`, `BENCH_TAG`.

## Reporting

```bash
python3.14 run.py report           # all benches, color if TTY
python3.14 run.py report arch      # one phase
python3.14 run.py report --out results/my_board.md
```

Writes a markdown leaderboard to `results/REPORT.md` (or `--out`) and prints a bar-chart table to the terminal.

## Published compare notes (Ollama, Jul 2026)

- `results/compare_pyhard_64k.md` — Pyhard @16k predict
- `results/compare_pyhard_hibudget.md` — Pyhard @49k
- `results/compare_pyhard_rerun.md` — Next/30B re-run
- `results/pyhard_failure_autopsy.md` — partial-credit rescoring
- `results/archbench/compare_archbench.md` — arch + claim leaderboard

## Notes

- Archbench’s Ollama tool protocol uses `<arch_tool>` / `<arch_final>` (not `<tool_call>`) so Qwen tool parsers do not EOF the server.
- Evidence points require **actual tool reads** (`files_read`). Citations alone do not count (Cursor ask-mode currently scores 0 evidence — honest, not a free lunch).
- Pyhard graders award **per-case partial credit** (first failure no longer zeros the task).
- Prefer `run.py` / `BENCH_OUT` over ancient `$HOME/.ollama/bench` paths in old notes.
