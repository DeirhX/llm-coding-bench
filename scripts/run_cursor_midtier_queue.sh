#!/bin/zsh
# Sequential Cursor suites for mid-tier models (near local MoE band, not frontier).
# Does not use local GPU — safe alongside Ollama think runs.
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export BENCH_OUT="${BENCH_OUT:-$ROOT/results}"
mkdir -p "$BENCH_OUT"
LOG="$BENCH_OUT/cursor_midtier_queue.log"
# Create before any follower attaches (avoids tail -f race).
: >>"$LOG"
exec >>"$LOG" 2>&1

# Mid-tier picks vs local tops (~88–96 pyhard):
#   gpt-5.3-codex     — coding specialist, default effort (not xhigh)
#   gpt-5.4-mini-high — mini class, not full 5.4
#   gemini-3.6-flash-high — flash, not 3.1 Pro
#   cursor-grok-4.5-medium — mid effort Grok
models=(
  'gpt-5.3-codex'
  'gpt-5.4-mini-high'
  'gemini-3.6-flash-high'
  'cursor-grok-4.5-medium'
)

echo "==== cursor midtier queue start $(date) ===="
for model in "${models[@]}"; do
  echo "---- suite $model $(date) ----"
  /bin/zsh "$ROOT/scripts/run_cursor_suite.sh" "$model" || echo "WARN suite failed model=$model"
  echo "---- suite done $model $(date) ----"
done
echo "==== cursor midtier queue ALL DONE $(date) ===="
