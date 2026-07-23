#!/bin/zsh
# Sequential Cursor repohard runs. Restores fixture between models.
# Usage: ./scripts/run_repohard_cursor_queue.sh model1 model2 ...
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG="${BENCH_QUEUE_LOG:-$ROOT/results/repohard/cursor_queue.log}"
mkdir -p "$ROOT/results/repohard"
: >>"$LOG"

if [[ $# -lt 1 ]]; then
  echo "usage: $0 model [model...]" >>"$LOG"
  exit 2
fi

echo "==== queue start $(date) models=$* ====" >>"$LOG"
for MODEL in "$@"; do
  echo "==== queue next $(date) model=$MODEL ====" >>"$LOG"
  git -C "$ROOT" checkout -- benches/repohard/fixture/ledgerkit/ >>"$LOG" 2>&1 || true
  "$ROOT/scripts/run_repohard_cursor.sh" "$MODEL" || {
    echo "==== queue WARN model failed $(date) model=$MODEL rc=$? ====" >>"$LOG"
  }
done
echo "==== queue done $(date) ====" >>"$LOG"
