#!/bin/zsh
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export BENCH_OUT="${BENCH_OUT:-$ROOT/results}"
LOG="$BENCH_OUT/pyhard_resume_wrapper.log"
mkdir -p "$BENCH_OUT"
exec >>"$LOG" 2>&1
echo "==== start $(date) ===="
"$ROOT/scripts/run_hard_bench_py.sh" "$@"
echo "==== done $(date) ===="
