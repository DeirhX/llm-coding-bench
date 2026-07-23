#!/bin/zsh
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
LOG="$ROOT/results/cursor_composer25_suite.log"
mkdir -p "$ROOT/results"
exec >>"$LOG" 2>&1

echo "==== cursor suite start $(date) model=composer-2.5 ===="
caffeinate -dims -w $$ &
trap 'kill %1 2>/dev/null || true' EXIT

PY="$(command -v python3.14)"
agent status >/dev/null || { echo "not logged in"; exit 1; }

export BENCH_PROVIDER=cursor
export BENCH_MODEL=composer-2.5
export BENCH_OUT="$ROOT/results"
export BENCH_CURSOR_MODE=ask

echo "---- pyhard ----"
BENCH_TAG=cursor_composer-2.5_pyhard "$PY" -u "$ROOT/hard_bench_py.py" || echo "WARN pyhard failed"

echo "---- archbench ----"
BENCH_TAG=cursor_composer-2.5_arch "$PY" -u "$ROOT/archbench/arch_bench.py" || echo "WARN arch failed"

echo "---- claim ----"
BENCH_TAG=cursor_composer-2.5_claim "$PY" -u "$ROOT/archbench/claim_bench.py" || echo "WARN claim failed"

echo "==== cursor suite done $(date) ===="
