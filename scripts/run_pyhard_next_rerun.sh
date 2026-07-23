#!/bin/zsh
# Wait for archbench-all to finish, then re-run pyhard coding bench for Next
# (and 30B as the efficiency twin for a clean head-to-head).
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export BENCH_OUT="${BENCH_OUT:-$ROOT/results}"

LOG="$BENCH_OUT/pyhard_next_rerun_wrapper.log"
mkdir -p "$BENCH_OUT"
exec >>"$LOG" 2>&1

echo "==== pyhard next-rerun waiter start $(date) ===="
ARCH_LOG="$BENCH_OUT/archbench/archbench_all_wrapper.log"

for i in $(seq 1 1440); do
  if grep -q '^==== archbench-all done' "$ARCH_LOG" 2>/dev/null \
     && ! pgrep -f 'benches.arch|run.py run arch' >/dev/null 2>&1 \
     && ! pgrep -f 'run_archbench_all.sh' >/dev/null 2>&1 \
     && ! pgrep -f 'benches.claim|run.py run claim' >/dev/null 2>&1; then
    echo "archbench idle $(date)"
    break
  fi
  if (( i % 10 == 0 )); then
    echo "still waiting ($i) $(date)"
    pgrep -lf 'benches.arch|run_archbench|benches.claim|run.py' | head -5 || echo '(no arch procs)'
  fi
  sleep 30
done

sleep 10
if pgrep -f 'benches.pyhard|run.py run pyhard' >/dev/null 2>&1; then
  echo "another pyhard still running — waiting..."
  while pgrep -f 'benches.pyhard|run.py run pyhard' >/dev/null 2>&1; do sleep 30; done
fi

echo "==== pyhard next-rerun start $(date) ===="
export BENCH_NUM_CTX=65536
export BENCH_NUM_PREDICT=16384
PY="$(command -v python3.14 || true)"
[[ -n "$PY" ]] || PY="$(uv python find 3.14)"

for model in 'qwen3-coder-next:q8_0' 'qwen3-coder:30b-a3b-fp16'; do
  tag="$(echo "$model" | sed 's/[^a-zA-Z0-9._-]/_/g')_pyhard_rerun"
  echo "---- $model tag=$tag ----"
  BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" "$ROOT/run.py" run pyhard \
    || echo "WARN: failed $model"
done

"$PY" "$ROOT/run.py" report pyhard --no-color || true
echo "==== pyhard next-rerun done $(date) ===="
