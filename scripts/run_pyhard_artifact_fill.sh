#!/bin/zsh
# Re-run only failed pyhard tasks that lack __code.py artifacts, merge into latest JSON,
# then rescore. Grouped by model to limit reload thrash.
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${BENCH_PYTHON:-}"
if [[ -z "$PY" ]]; then
  if command -v uv >/dev/null 2>&1; then
    PY="$(uv python find 3.14)"
  else
    PY="$(command -v python3.14 || true)"
  fi
fi
if [[ -z "$PY" || ! -x "$PY" ]]; then
  echo "Need Python 3.14" >&2
  exit 1
fi

export BENCH_OUT="${BENCH_OUT:-$ROOT/results}"
mkdir -p "$BENCH_OUT"
LOG="$BENCH_OUT/pyhard_artifact_fill.log"
# Create before any follower attaches (avoids tail -f race).
: >>"$LOG"
exec >>"$LOG" 2>&1

run_fill() {
  local model="$1" tag="$2" tasks="$3" think="$4" predict="$5"
  echo "==== fill model=$model tag=$tag tasks=$tasks think=$think predict=$predict $(date) ===="
  BENCH_MODEL="$model" \
    BENCH_TAG="$tag" \
    BENCH_TASKS="$tasks" \
    BENCH_MERGE_LATEST=1 \
    BENCH_THINK="$think" \
    BENCH_NUM_CTX=65536 \
    BENCH_NUM_PREDICT="$predict" \
    "$PY" -u "$ROOT/run.py" run pyhard
  echo "==== done tag=$tag $(date) ===="
}

echo "==== pyhard artifact fill start $(date) ===="

# North (think-on historically) — already in ollama tags; keep think=1 to match prior runs.
run_fill 'north-mini-code-1.0:bf16' 'north-mini-code-1.0_bf16_pyhard' \
  'eval_expr,sat_solve,json_patch,mini_sql' 1 16384
run_fill 'north-mini-code-1.0:bf16' 'north-mini-code-1.0_bf16_pyhard_p49k' \
  'mini_sql' 1 49152

# Coder reruns (no native think)
run_fill 'qwen3-coder-next:q8_0' 'qwen3-coder-next_q8_0_pyhard_rerun' \
  'eval_expr,unify' 0 16384
run_fill 'qwen3-coder:30b-a3b-fp16' 'qwen3-coder_30b-a3b-fp16_pyhard_rerun' \
  'sat_solve,mini_sql' 0 16384

# Qwen think-on (canvas rows) + 3.6 @49k predict
run_fill 'qwen3.5:35b-a3b-coding-bf16' 'qwen3.5_35b-a3b-coding-bf16_pyhard' \
  'lru_cache,float_vm,json_patch,unify' 1 16384
run_fill 'qwen3.6:35b-a3b-coding-bf16' 'qwen3.6_35b-a3b-coding-bf16_pyhard' \
  'regex_match,eval_expr,float_vm,sat_solve' 1 16384
run_fill 'qwen3.6:35b-a3b-coding-bf16' 'qwen3.6_35b-a3b-coding-bf16_pyhard_p49k' \
  'eval_expr,float_vm,sat_solve,unify' 1 49152

echo "==== rescore $(date) ===="
BENCH_SELFTEST=1 "$PY" -m benches.pyhard.rescore
echo "==== pyhard artifact fill ALL DONE $(date) ===="
