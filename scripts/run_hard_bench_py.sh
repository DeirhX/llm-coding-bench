#!/bin/zsh
# Run Python-3.14 hard bench for one or more models.
# Usage:
#   ./scripts/run_hard_bench_py.sh
#   ./scripts/run_hard_bench_py.sh 'qwen3-coder-next:q8_0' 'gpt-oss:120b'
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
  echo "Need Python 3.14 (install via: uv python install 3.14)" >&2
  exit 1
fi

models=("$@")
if (( $# == 0 )); then
  models=(
    'qwen3-coder-next:q8_0'
    'qwen3-coder:30b-a3b-fp16'
    'gpt-oss:120b'
    'qwen3.5:35b-a3b-coding-bf16'
  )
fi

export BENCH_OUT="${BENCH_OUT:-$ROOT/results}"
mkdir -p "$BENCH_OUT"
LOG="$BENCH_OUT/hard_bench_py_runner.log"
exec > >(tee -a "$LOG") 2>&1

echo "==== pyhard start $(date) ===="
echo "python=$PY"
"$PY" -c 'import sys; print(sys.version)'

for model in "${models[@]}"; do
  tag="$(echo "$model" | sed 's/[^a-zA-Z0-9._-]/_/g')_pyhard"
  echo "---- $model (tag=$tag) ----"
  BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" "$ROOT/run.py" run pyhard
done

"$PY" "$ROOT/run.py" report pyhard --no-color
echo "==== pyhard done $(date) ===="
