#!/bin/zsh
# Repohard with anti-rumination think policy:
#   - think=low (tighter than medium)
#   - think only round 0 (tool/patch rounds think-off)
#   - hard think char budget (abort + promote/nudge)
#   - promote closed <arch_final> drafted inside thinking
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"
# Do not inherit a leftover BENCH_TAG / MERGE from the parent shell — that
# already made one "run" skip every task into someone else's gemini tag. Nice.
TAG="${1:-qwen3.6_35b-a3b-coding-bf16_repohard_think_tight_r1}"
LOG="$ROOT/results/repohard/ollama_qwen36_think_tight.log"
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== qwen3.6 repohard think_tight $(date) ===="
git -C "$ROOT" checkout -- benches/repohard/fixture/ledgerkit/ || true

unset BENCH_TAG BENCH_MERGE_LATEST BENCH_TASKS
export BENCH_PROVIDER=ollama
export BENCH_MODEL='qwen3.6:35b-a3b-coding-bf16'
export BENCH_THINK=low
export BENCH_THINK_ROUNDS=1
export BENCH_THINK_MAX_CHARS=8192
export BENCH_THINK_PROMOTE=1
export BENCH_THINK_LOOP=1
export BENCH_NUM_CTX=65536
export BENCH_NUM_PREDICT=16384
export BENCH_MAX_ROUNDS=40
export BENCH_MAX_TOOL_CALLS=40
export BENCH_FINALIZE_AFTER=0
export BENCH_TASK_TIMEOUT_S=1200
export BENCH_OUT="$ROOT/results"
export BENCH_TAG="$TAG"
export BENCH_MERGE_LATEST=0

echo "THINK=$BENCH_THINK ROUNDS=$BENCH_THINK_ROUNDS MAX_CHARS=$BENCH_THINK_MAX_CHARS PREDICT=$BENCH_NUM_PREDICT TAG=$BENCH_TAG"
"$PY" -u "$ROOT/run.py" run repohard || echo "WARN run rc=$?"
echo "==== qwen3.6 repohard think_tight done $(date) ===="
"$PY" -u "$ROOT/run.py" report repohard --no-color || true
