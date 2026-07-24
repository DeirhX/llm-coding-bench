#!/bin/zsh
# Thin launcher — real logic in run_cursor_repohard_stale.py
set -uo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"
OUT="$ROOT/results/repohard"
LOG="$OUT/cursor_repohard_stale.log"
mkdir -p "$OUT"
: >>"$LOG"
exec >>"$LOG" 2>&1
export BENCH_PARALLEL="${BENCH_PARALLEL:-2}"
export BENCH_WAIT_MATRIX="${BENCH_WAIT_MATRIX:-0}"
exec "$PY" -u "$ROOT/scripts/run_cursor_repohard_stale.py"
