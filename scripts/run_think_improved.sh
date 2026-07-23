#!/bin/zsh
# Re-run thinking benches with improved Ollama harness:
#   - BENCH_THINK=medium (bounded CoT, not unbounded default)
#   - auto num_predict=49152 (pyhard) / 24576 (arch) unless overridden
#   - grade answer content only (no scraping truncated thinking)
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
[[ -n "$PY" && -x "$PY" ]] || { echo "Need Python 3.14" >&2; exit 1; }

export BENCH_OUT="${BENCH_OUT:-$ROOT/results}"
mkdir -p "$BENCH_OUT" "$BENCH_OUT/archbench"
LOG="$BENCH_OUT/think_improved.log"
# Create before any follower attaches (avoids tail -f race).
: >>"$LOG"
exec >>"$LOG" 2>&1

export BENCH_THINK=medium
export BENCH_NUM_CTX=65536
# Explicit so logs show intent; matches new think-on default for pyhard.
export BENCH_NUM_PREDICT="${BENCH_NUM_PREDICT:-49152}"

echo "==== think-improved start $(date) ===="
echo "BENCH_THINK=$BENCH_THINK BENCH_NUM_PREDICT=$BENCH_NUM_PREDICT BENCH_NUM_CTX=$BENCH_NUM_CTX"

run_pyhard() {
  local model="$1" tag="$2"
  echo "---- pyhard $model tag=$tag $(date) ----"
  BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" -u "$ROOT/run.py" run pyhard
  echo "---- pyhard done $tag $(date) ----"
}

run_arch() {
  local model="$1" tag="$2"
  echo "---- arch $model tag=$tag $(date) ----"
  # Arch turns need less than full 49k; 24k is the think default if unset — keep 24k here.
  BENCH_MODEL="$model" BENCH_TAG="$tag" BENCH_NUM_PREDICT=24576 \
    "$PY" -u "$ROOT/run.py" run arch
  echo "---- arch done $tag $(date) ----"
}

# Full think-on pyhard (overwrite previous truncated think-on tags)
run_pyhard 'qwen3.5:35b-a3b-coding-bf16' 'qwen3.5_35b-a3b-coding-bf16_pyhard'
run_pyhard 'qwen3.6:35b-a3b-coding-bf16' 'qwen3.6_35b-a3b-coding-bf16_pyhard'

# Arch think-on (3.6 hung previously — retry with medium + 24k)
run_arch 'qwen3.5:35b-a3b-coding-bf16' 'qwen3.5_35b-a3b-coding-bf16_arch_think'
run_arch 'qwen3.6:35b-a3b-coding-bf16' 'qwen3.6_35b-a3b-coding-bf16_arch_think'

echo "==== rescore $(date) ===="
BENCH_SELFTEST=1 "$PY" -m benches.pyhard.rescore || true
BENCH_SELFTEST=1 "$PY" -m benches.arch.rescore || true

echo "==== think-improved ALL DONE $(date) ===="
