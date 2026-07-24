#!/bin/zsh
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"
LOG="$ROOT/results/repohard/ollama_qwen36_think_medium_r2.log"
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== qwen3.6 repohard think_medium r2 resume (multi-line loop break) $(date) ===="
git -C "$ROOT" checkout -- benches/repohard/fixture/ledgerkit/ || true

export BENCH_PROVIDER=ollama
export BENCH_MODEL='qwen3.6:35b-a3b-coding-bf16'
export BENCH_THINK=medium
export BENCH_NUM_CTX=65536
export BENCH_NUM_PREDICT=24576
export BENCH_MAX_ROUNDS=40
export BENCH_MAX_TOOL_CALLS=40
export BENCH_FINALIZE_AFTER=0
export BENCH_TASK_TIMEOUT_S=1200
export BENCH_OUT="$ROOT/results"
export BENCH_TAG='qwen3.6_35b-a3b-coding-bf16_repohard_think_medium_r2'
export BENCH_MERGE_LATEST=1
export BENCH_THINK_LOOP=1

echo "THINK=$BENCH_THINK MERGE=$BENCH_MERGE_LATEST TAG=$BENCH_TAG LOOP=$BENCH_THINK_LOOP"
"$PY" -u "$ROOT/run.py" run repohard || echo "WARN run rc=$?"
echo "==== qwen3.6 repohard FULL think_medium r2 done $(date) ===="
"$PY" -u "$ROOT/run.py" report repohard --no-color || true
