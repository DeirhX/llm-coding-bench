#!/bin/zsh
# Wait for the current resume bench to finish, then rerun thinking-heavy models
# with a larger num_predict budget.
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export BENCH_OUT="${BENCH_OUT:-$ROOT/results}"

LOG="$BENCH_OUT/pyhard_hibudget_wrapper.log"
mkdir -p "$BENCH_OUT"
exec >>"$LOG" 2>&1
echo "==== hibudget waiter start $(date) ===="

RESUME_LOG="$BENCH_OUT/pyhard_resume_wrapper.log"
for i in $(seq 1 720); do  # up to ~6h at 30s
  if grep -q '^==== resume done' "$RESUME_LOG" 2>/dev/null; then
    echo "resume done detected $(date)"
    break
  fi
  if [[ -f "$BENCH_OUT/qwen3.6_35b-a3b-coding-bf16_pyhard_pyhard_latest.json" \
     && -f "$BENCH_OUT/north-mini-code-1.0_bf16_pyhard_pyhard_latest.json" ]] \
     && ! pgrep -f 'benches.pyhard|run.py run pyhard' >/dev/null 2>&1; then
    echo "latest results present and bench idle $(date)"
    break
  fi
  if (( i % 10 == 0 )); then
    echo "still waiting ($i) $(date)"
    pgrep -lf 'benches.pyhard|run.py' | head -3 || echo '(no bench proc)'
  fi
  sleep 30
done

sleep 5
if pgrep -f 'benches.pyhard|run.py run pyhard' >/dev/null 2>&1; then
  echo "bench still running — waiting more..."
  while pgrep -f 'benches.pyhard|run.py run pyhard' >/dev/null 2>&1; do sleep 30; done
fi

echo "==== hibudget bench start $(date) ===="
export BENCH_NUM_PREDICT=49152
export BENCH_NUM_CTX=65536
echo "BENCH_NUM_PREDICT=$BENCH_NUM_PREDICT BENCH_NUM_CTX=$BENCH_NUM_CTX"

PY="$(command -v python3.14 || true)"
[[ -n "$PY" ]] || PY="$(uv python find 3.14)"

for model in 'qwen3.6:35b-a3b-coding-bf16' 'north-mini-code-1.0:bf16'; do
  tag="$(echo "$model" | sed 's/[^a-zA-Z0-9._-]/_/g')_pyhard_p49k"
  echo "---- $model tag=$tag ----"
  BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" "$ROOT/run.py" run pyhard
done

"$PY" "$ROOT/run.py" report pyhard --no-color || true
echo "==== hibudget done $(date) ===="
