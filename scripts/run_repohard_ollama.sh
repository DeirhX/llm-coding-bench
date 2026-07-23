#!/bin/zsh
# Run repohard via local Ollama (think-off by default).
# Usage: ./scripts/run_repohard_ollama.sh [model]
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MODEL="${1:-${BENCH_MODEL:?model required}}"
SAFE="$(echo "$MODEL" | sed 's/[^a-zA-Z0-9._-]/_/g')"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"

export BENCH_OUT="${BENCH_OUT:-$ROOT/results}"
mkdir -p "$BENCH_OUT/repohard"
LOG="$BENCH_OUT/repohard/ollama_${SAFE}_repohard_suite.log"
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== repohard ollama start $(date) model=$MODEL ===="
"$PY" -c 'import pytest; print("pytest", pytest.__version__)'

export BENCH_PROVIDER=ollama
export BENCH_MODEL="$MODEL"
export BENCH_THINK="${BENCH_THINK:-0}"
export BENCH_NUM_CTX="${BENCH_NUM_CTX:-65536}"
export BENCH_TAG="${BENCH_TAG:-${SAFE}_repohard}"

BENCH_TAG="$BENCH_TAG" "$PY" -u "$ROOT/run.py" run repohard
echo "==== repohard ollama done $(date) model=$MODEL ===="
"$PY" -u "$ROOT/run.py" report repohard --no-color || true
