#!/bin/zsh
# Wait for a log file to exist, then tail -F it.
# Fixes the race where `tail -f` is started before the producer creates the file.
#
# Usage: ./scripts/follow_log.sh PATH [max_wait_s]
set -euo pipefail
LOG="${1:?usage: follow_log.sh PATH [max_wait_s]}"
MAX_WAIT="${2:-60}"

deadline=$((SECONDS + MAX_WAIT))
while [[ ! -e "$LOG" ]]; do
  if (( SECONDS >= deadline )); then
    echo "follow_log: timed out waiting for $LOG (${MAX_WAIT}s)" >&2
    exit 1
  fi
  sleep 0.1
done

# -F retries if the file is rotated/replaced; -n0 only new lines after attach.
exec tail -n0 -F "$LOG"
