#!/bin/zsh
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export BENCH_OUT="${BENCH_OUT:-$ROOT/results}"
LOG="$BENCH_OUT/pyhard_qwens_wrapper.log"
mkdir -p "$BENCH_OUT"
exec >>"$LOG" 2>&1
echo "==== start $(date) ===="
"$ROOT/scripts/run_hard_bench_py.sh" \
  'qwen3-coder-next:q8_0' \
  'qwen3-coder:30b-a3b-fp16' \
  'qwen2.5-coder:32b-instruct-q8_0'
echo "==== done $(date) ===="
