#!/bin/zsh
# Ablation-winning think_medium + anti-rumination caps (no round gating).
# Evidence: think_tight (low + ROUNDS=1) crashed to 40/80; medium was 60/80.
# Keep: char budget abort, promote closed arch_final from thinking, loop breaker.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"
TAG="${1:-qwen3.6_35b-a3b-coding-bf16_repohard_think_budget_r1}"
LOG="$ROOT/results/repohard/ollama_qwen36_think_budget.log"
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== qwen3.6 repohard think_budget $(date) ===="
git -C "$ROOT" checkout -- benches/repohard/fixture/ledgerkit/ || true

unset BENCH_TAG BENCH_MERGE_LATEST BENCH_TASKS
export BENCH_PROVIDER=ollama
export BENCH_MODEL='qwen3.6:35b-a3b-coding-bf16'
export BENCH_THINK=medium
# Think every round — ROUNDS=1 was the lobotomy that nuked race/nplus1/deputy.
unset BENCH_THINK_ROUNDS
export BENCH_THINK_MAX_CHARS=8192
export BENCH_THINK_PROMOTE=1
export BENCH_THINK_LOOP=1
export BENCH_NUM_CTX=65536
export BENCH_NUM_PREDICT=24576
export BENCH_MAX_ROUNDS=40
export BENCH_MAX_TOOL_CALLS=40
export BENCH_FINALIZE_AFTER=0
export BENCH_TASK_TIMEOUT_S=1200
export BENCH_OUT="$ROOT/results"
export BENCH_TAG="$TAG"
export BENCH_MERGE_LATEST=0

echo "THINK=$BENCH_THINK ROUNDS=${BENCH_THINK_ROUNDS:-all} MAX_CHARS=$BENCH_THINK_MAX_CHARS PREDICT=$BENCH_NUM_PREDICT TAG=$BENCH_TAG"
"$PY" -u "$ROOT/run.py" run repohard || echo "WARN run rc=$?"
echo "==== qwen3.6 repohard think_budget done $(date) ===="
"$PY" -u "$ROOT/run.py" report repohard --no-color || true
