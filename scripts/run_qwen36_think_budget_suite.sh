#!/bin/zsh
# Universal think_budget policy (repohard winner: 67/80):
#   think=medium, MAX_CHARS=8192, promote+loop on, ctx=64k, predict=24k
# Runs claim → arch → pyhard for qwen3.6 (repohard already done).
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"
LOG="$ROOT/results/ollama_qwen36_think_budget_suite.log"
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== qwen3.6 think_budget suite (claim/arch/pyhard) $(date) ===="

unset BENCH_TAG BENCH_MERGE_LATEST BENCH_TASKS
export BENCH_PROVIDER=ollama
export BENCH_MODEL='qwen3.6:35b-a3b-coding-bf16'
export BENCH_THINK=medium
unset BENCH_THINK_ROUNDS
export BENCH_THINK_MAX_CHARS=8192
export BENCH_THINK_PROMOTE=1
export BENCH_THINK_LOOP=1
export BENCH_NUM_CTX=65536
export BENCH_NUM_PREDICT=24576
export BENCH_TASK_TIMEOUT_S=1200
export BENCH_OUT="$ROOT/results"
export BENCH_MERGE_LATEST=0

echo "policy THINK=$BENCH_THINK MAX_CHARS=$BENCH_THINK_MAX_CHARS PREDICT=$BENCH_NUM_PREDICT CTX=$BENCH_NUM_CTX"

run_one() {
  local bench="$1" tag="$2"
  echo "---- $bench tag=$tag $(date) ----"
  export BENCH_TAG="$tag"
  "$PY" -u "$ROOT/run.py" run "$bench" || echo "WARN $bench rc=$?"
  echo "---- $bench done $(date) ----"
  "$PY" -u "$ROOT/run.py" report "$bench" --no-color || true
}

# Smallest first so we get an early signal.
run_one claim 'qwen3.6_35b-a3b-coding-bf16_claim_think_budget'
run_one arch  'qwen3.6_35b-a3b-coding-bf16_arch_think_budget'
run_one pyhard 'qwen3.6_35b-a3b-coding-bf16_pyhard_think_budget'

echo "==== qwen3.6 think_budget suite ALL DONE $(date) ===="
echo "Compare vs prior: claim ~18/23?, arch think 73/90 / off 84–89, pyhard think ~57 / off ~95"
echo "repohard think_budget already: 67/80"
