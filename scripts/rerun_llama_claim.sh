#!/bin/zsh
# After pyhard next-rerun finishes: re-run hung llama claim + rewrite compare.
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export BENCH_OUT="${BENCH_OUT:-$ROOT/results}"
LOG="$BENCH_OUT/archbench/llama_claim_rerun.log"
mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1

echo "==== llama claim re-run waiter start $(date) ===="

caffeinate -dims -w $$ &
CAFFEINE_PID=$!
trap 'kill $CAFFEINE_PID 2>/dev/null || true' EXIT

PYHARD_LOG="$BENCH_OUT/pyhard_next_rerun_wrapper.log"
for i in $(seq 1 1440); do
  if grep -q '^==== pyhard next-rerun done' "$PYHARD_LOG" 2>/dev/null \
     && ! pgrep -f 'benches.pyhard|run.py run pyhard' >/dev/null 2>&1 \
     && ! pgrep -f 'run_pyhard_next_rerun.sh' >/dev/null 2>&1; then
    echo "pyhard idle $(date)"
    break
  fi
  if (( i % 10 == 0 )); then
    echo "still waiting pyhard ($i) $(date)"
    pgrep -lf 'benches.pyhard|run_pyhard|run.py' | head -5 || echo '(no pyhard procs)'
  fi
  sleep 30
done

sleep 5

OUT="$BENCH_OUT/archbench"
PY="$(command -v python3.14)"
MODEL='llama3.3:70b-instruct-q8_0'
TAG='llama3.3_70b-instruct-q8_0_claim'

rm -f "$OUT/${TAG}_latest.json" "$OUT/${TAG}"_20*.json 2>/dev/null || true

echo "==== llama claim start $(date) ===="
BENCH_MODEL="$MODEL" BENCH_TAG="$TAG" "$PY" "$ROOT/run.py" run claim \
  || echo "WARN: llama claim failed exit=$?"

echo "==== rewrite compare $(date) ===="
"$PY" -m benches.arch.analyze
"$PY" "$ROOT/run.py" report --no-color || true

echo "==== llama claim re-run done $(date) ===="
