#!/bin/zsh
# After pyhard next-rerun finishes: re-run hung llama claim + rewrite compare.
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"

LOG="$HOME/.ollama/bench/results/archbench/llama_claim_rerun.log"
mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1

echo "==== llama claim re-run waiter start $(date) ===="

# Stay awake for the whole job (display can dim; system must not sleep).
# Parent should already wrap us in caffeinate; keep a nested one as belt+suspenders.
caffeinate -dims -w $$ &
CAFFEINE_PID=$!
trap 'kill $CAFFEINE_PID 2>/dev/null || true' EXIT

PYHARD_LOG="$HOME/.ollama/bench/results/pyhard_next_rerun_wrapper.log"
for i in $(seq 1 1440); do
  if grep -q '^==== pyhard next-rerun done' "$PYHARD_LOG" 2>/dev/null \
     && ! pgrep -f 'hard_bench_py.py' >/dev/null 2>&1 \
     && ! pgrep -f 'run_pyhard_next_rerun.sh' >/dev/null 2>&1; then
    echo "pyhard idle $(date)"
    break
  fi
  if (( i % 10 == 0 )); then
    echo "still waiting pyhard ($i) $(date)"
    pgrep -lf 'hard_bench_py|run_pyhard' | head -5 || echo '(no pyhard procs)'
  fi
  sleep 30
done

# clear any leftover model lock
sleep 5

ROOT="$HOME/.ollama/bench/archbench"
OUT="$HOME/.ollama/bench/results/archbench"
PY="$(command -v python3.14)"
MODEL='llama3.3:70b-instruct-q8_0'
TAG='llama3.3_70b-instruct-q8_0_claim'

# wipe prior partial / missing so analyze picks up the fresh run
rm -f "$OUT/${TAG}_latest.json" "$OUT/${TAG}"_20*.json 2>/dev/null || true

echo "==== llama claim start $(date) ===="
BENCH_MODEL="$MODEL" BENCH_TAG="$TAG" "$PY" "$ROOT/claim_bench.py" \
  || echo "WARN: llama claim failed exit=$?"

echo "==== rewrite compare $(date) ===="
# analyze skips existing claims; llama is the only missing/refreshed one
"$PY" "$ROOT/analyze_arch_results.py"

echo "==== llama claim re-run done $(date) ===="
