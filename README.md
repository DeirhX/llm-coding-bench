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
  repohard/
    assignment/*.md   # ← issue text
    fixture/ledgerkit/# synthetic multi-package product (agent workspace)
    private/          # NEVER on agent path — hidden pytest + gold patches
    tools.py / tasks.py / bench.py
  audittrap/
    claims.yaml + assignment/*.md
    fixture/miniharness/  # synthetic harness with real bugs + lying notes
    private/              # hidden pytest + gold patches
    tools.py / tasks.py / bench.py
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
| **Repohard** | `repohard` | `benches/repohard/assignment/*.md` + synthetic ledgerkit | Explore → unified diff; private pytest (8 / 80) |
| **Audittrap** | `audittrap` | `benches/audittrap/` + synthetic miniharness | Claim traps + fix/won't-fix (7 / ~81) |

Graders stay in Python (they must execute). Prompts/claims are data.

### Adding a new bench

1. Create `benches/<id>/` with `assignment/` (or equivalent data) + `bench.py` exposing `main()`
2. Add `__main__.py` and register a `BenchMetadata` in `benches/registry.py`
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

- Python **3.14+** (pyhard / archbench / repohard graders)
- `pytest` (repohard private grading)

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

Or per-phase modules (requires `BENCH_MODEL` or `BENCH_SELFTEST=1` to avoid warmup failures):

```bash
BENCH_SELFTEST=1 python3.14 -m benches.pyhard
BENCH_SELFTEST=1 python3.14 -m benches.arch
BENCH_SELFTEST=1 python3.14 -m benches.claim
BENCH_SELFTEST=1 python3.14 -m benches.repohard
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
- Repohard points `--workspace` at `benches/repohard/fixture/ledgerkit` (never `private/`); model must return a unified diff in `<arch_final>`
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

## Hardware, runners and operations

- `M5_MAX_128GB_VIABILITY.md` — **which** models fit an M5 Max 128 GB
- `RUNNERS_MACOS_METAL.md` — **what** to run them with (Ollama's dual engine, MLX vs llama.cpp)
- `LOCAL_AGENT_OPS.md` — **operating one for real work**: prefix-cache economics, Claude Code's
  undocumented behaviour (auto-compaction cannot fire), the framing tax, the edit-tool failure
  taxonomy, and the wrong hypotheses that cost us the most. Read before debugging a slow session.
- `DEPTH_PIPELINE_PLAN.md` — the plan of record for the evidence-gated agent pipeline built on
  those constraints: decisions taken, the measurements that forced them, and what would reverse them
- `scripts/claude-gemma.sh` — launcher; `.cursor/skills/ollama-watch/` — live Ollama diagnosis
- `scripts/phase0/` — the probes behind every **[measured]** claim in the two documents above

### The depth gate

A local 31B fails the same way on unrelated tasks: it stops exploring once it has a story that
sounds complete, then asserts it with confident specifics. Prompts do not fix that — this repository
measured the same thing for context discipline — so the stopping decision is moved out of the model.
An answer is refused unless its claims carry evidence that survives being looked up.

- `claude-gemma --depth [review|debug|refactor-proposal|ops-perf|bench-audit]` — gate a session.
  The contract goes in with your first prompt; a thin answer is refused once, with every gap in one
  message. `touch /tmp/cc-depth-off` lifts it without editing anything.
- `scripts/depth_pipeline.py` — the same verifier, unattended, as sequential stages.
- `scripts/depth_fixtures.py` — runs the gate against two real answers this project already
  produced: the refactor proposal it exists to refuse, and five findings it must not cost.

Delegates are gated too: `SubagentStop` judges each one on its own answer, and its verdict lands in
`artifacts/depth/<session>/<agent_id>/gate.json` beside the parent's.

### Reaching a non-Ollama backend

`scripts/anthropic_proxy.py` speaks Anthropic Messages in front and OpenAI chat completions behind,
so Claude Code can drive `llama-server` — streaming, tools and reasoning output included. Two
reasons to run it even against Ollama: it logs every request **as it arrives**, with the caller's
`user-agent`, which is the attribution the server log has never had; and `--force-model` pins the
model at the port, after the client was caught asking for `claude-opus-4-8` regardless of every
model setting. On Ollama a wrong-but-existing model name unloads 62 GB of resident weights.

### Known limitations (Claude Code + local Ollama)

These are measured gaps, not TODOs we forgot to file:

- **Auto-compaction cannot fire** without an Anthropic account / feature-gate fetch, and Ollama
  never returns “prompt is too long”. Use `/compact` by hand; the status line nags at 60%.
- **`skeptic_min.md` under Claude Code’s own system prompt is unverified.** Every 20/20 trap score
  used that file as the *entire* system prompt.
- **No long soak** in a large repo has been graded end-to-end; verification was short tasks.
- **Thinking UI:** `showThinkingSummaries` shows summarized reasoning (`ctrl+o` expands;
  `Option+T` / `Alt+T` toggles). Prefill is silent in the UI — use `ollama-watch` / `state.py`.
- **Windows / resume:** `--model` and lean-tool / guard settings apply to **new** conversations;
  `--continue` keeps whatever the session was created with.

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
- **Repohard** uses a large *synthetic* ledgerkit fixture on purpose — no OSS fork contamination gamble. Private tests/gold never sit on the agent workspace path. Grade = private pytest only (v1).
- Prefer `run.py` / `BENCH_OUT` over ancient `$HOME/.ollama/bench` paths in old notes.
