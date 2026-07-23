#!/bin/zsh
# Full Cursor suite: pyhard → arch → claim
# Usage:
#   ./scripts/run_cursor_suite.sh                          # composer-2.5
#   ./scripts/run_cursor_suite.sh claude-sonnet-5-high
#   BENCH_MODEL=... ./scripts/run_cursor_suite.sh
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

MODEL="${1:-${BENCH_MODEL:-composer-2.5}}"
SAFE="$(echo "$MODEL" | sed 's/[^a-zA-Z0-9._-]/_/g')"
LOG="$ROOT/results/cursor_${SAFE}_suite.log"
mkdir -p "$ROOT/results"
exec >>"$LOG" 2>&1

echo "==== cursor suite start $(date) model=$MODEL ===="
caffeinate -dims -w $$ &
trap 'kill %1 2>/dev/null || true' EXIT

PY="$(command -v python3.14)"
agent status >/dev/null || { echo "not logged in"; exit 1; }

export BENCH_PROVIDER=cursor
export BENCH_MODEL="$MODEL"
export BENCH_OUT="$ROOT/results"
export BENCH_CURSOR_MODE=ask

echo "---- pyhard ----"
BENCH_TAG="cursor_${SAFE}_pyhard" "$PY" -u "$ROOT/run.py" run pyhard || echo "WARN pyhard failed"

echo "---- archbench ----"
BENCH_TAG="cursor_${SAFE}_arch" "$PY" -u "$ROOT/run.py" run arch || echo "WARN arch failed"

echo "---- claim ----"
BENCH_TAG="cursor_${SAFE}_claim" "$PY" -u "$ROOT/run.py" run claim || echo "WARN claim failed"

echo "==== cursor suite done $(date) model=$MODEL ===="
"$PY" -u "$ROOT/run.py" report --no-color || true
