#!/bin/zsh
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export BENCH_OUT="${BENCH_OUT:-$ROOT/results}"
LOG="$BENCH_OUT/pyhard_qwen35_36_wrapper.log"
mkdir -p "$BENCH_OUT"
exec >>"$LOG" 2>&1
echo "==== start $(date) ===="
"$ROOT/scripts/run_hard_bench_py.sh" \
  'qwen3.5:35b-a3b-coding-bf16' \
  'qwen3.6:35b-a3b-coding-bf16'
echo "==== done $(date) ===="
