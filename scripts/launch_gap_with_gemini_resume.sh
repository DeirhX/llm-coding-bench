#!/bin/zsh
# Resume incomplete gemini repohard, then continue the Cursor gap queue.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
export BENCH_PROVIDER=cursor
export BENCH_CURSOR_MODE="${BENCH_CURSOR_MODE:-ask}"
export BENCH_TASK_TIMEOUT_S="${BENCH_TASK_TIMEOUT_S:-600}"
export BENCH_CURSOR_TIMEOUT="${BENCH_CURSOR_TIMEOUT:-$BENCH_TASK_TIMEOUT_S}"
export BENCH_OUT="${BENCH_OUT:-$ROOT/results}"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
LOG="$BENCH_OUT/cursor_gap_queue.log"
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== gemini repohard MERGE resume $(date) ===="
BENCH_MODEL=gemini-3.6-flash-high \
  BENCH_TAG=cursor_gemini-3.6-flash-high_repohard \
  BENCH_MERGE_LATEST=1 \
  "$PY" -u "$ROOT/run.py" run repohard \
  || echo "WARN gemini repohard resume rc=$? $(date)"
echo "==== gemini repohard resume done $(date) ===="

exec /bin/zsh "$ROOT/scripts/run_cursor_gap_queue.sh" --from cursor-grok-4.5-medium
