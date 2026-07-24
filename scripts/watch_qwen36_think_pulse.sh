#!/bin/zsh
set -uo pipefail
ROOT="/Users/deirh/Projects/llm-coding-bench"
LOG="$ROOT/results/repohard/ollama_qwen36_think_medium_full.log"
count_marks() {
  grep -cE '^-- [a-z0-9_]+ \.\.\.|think_medium done|==== report' "$LOG" 2>/dev/null || echo 0
}
LAST_N=$(count_marks)
while true; do
  if [[ ! -f "$LOG" ]]; then sleep 5; continue; fi
  N=$(count_marks)
  if [[ "$N" -gt "$LAST_N" ]]; then
    LAST_N=$N
    echo 'AGENT_LOOP_WAKE_repohard_pulse {"prompt":"Pulse qwen3.6 think_medium full repohard: scores so far, current task, ETA vibe. Stop loop if suite done."}'
  fi
  PID=$(cat "$ROOT/results/repohard/qwen36_think_full.pid" 2>/dev/null || true)
  if [[ -n "$PID" ]] && ! kill -0 "$PID" 2>/dev/null; then
    echo 'AGENT_LOOP_WAKE_repohard_pulse {"prompt":"Suite process died — final pulse for qwen3.6 think_medium repohard."}'
    exit 0
  fi
  sleep 20
done
