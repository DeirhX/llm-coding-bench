#!/bin/zsh
# Run repohard via Cursor Agent CLI.
# Usage: ./scripts/run_repohard_cursor.sh [model]
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MODEL="${1:-${BENCH_MODEL:-composer-2.5}}"
SAFE="$(echo "$MODEL" | sed 's/[^a-zA-Z0-9._-]/_/g')"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"

export BENCH_OUT="${BENCH_OUT:-$ROOT/results}"
mkdir -p "$BENCH_OUT/repohard"
LOG="$BENCH_OUT/repohard/cursor_${SAFE}_repohard_suite.log"
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== repohard cursor start $(date) model=$MODEL ===="
agent status >/dev/null || { echo "not logged in"; exit 1; }
"$PY" -c 'import pytest; print("pytest", pytest.__version__)'

export BENCH_PROVIDER=cursor
export BENCH_MODEL="$MODEL"
export BENCH_CURSOR_MODE="${BENCH_CURSOR_MODE:-ask}"
export BENCH_TAG="cursor_${SAFE}_repohard"

BENCH_TAG="$BENCH_TAG" "$PY" -u "$ROOT/run.py" run repohard
echo "==== repohard cursor done $(date) model=$MODEL ===="
"$PY" -u "$ROOT/run.py" report repohard --no-color || true
