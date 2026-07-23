#!/bin/zsh
# Sequential Ollama repohard runs. Restores fixture between models.
# Usage: ./scripts/run_repohard_ollama_queue.sh model1 model2 ...
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
LOG="${BENCH_QUEUE_LOG:-$ROOT/results/repohard/ollama_queue.log}"
mkdir -p "$ROOT/results/repohard"
: >>"$LOG"

if [[ $# -lt 1 ]]; then
  echo "usage: $0 model [model...]" >>"$LOG"
  exit 2
fi

echo "==== ollama queue start $(date) models=$* ====" >>"$LOG"
for MODEL in "$@"; do
  echo "==== ollama queue next $(date) model=$MODEL ====" >>"$LOG"
  git -C "$ROOT" checkout -- benches/repohard/fixture/ledgerkit/ >>"$LOG" 2>&1 || true
  # unload prior model to free VRAM before next load
  if command -v ollama >/dev/null 2>&1; then
    ollama stop "$(ollama ps 2>/dev/null | awk 'NR==2{print $1}')" >>"$LOG" 2>&1 || true
  fi
  "$ROOT/scripts/run_repohard_ollama.sh" "$MODEL" || {
    echo "==== ollama queue WARN failed $(date) model=$MODEL ====" >>"$LOG"
  }
done
echo "==== ollama queue done $(date) ====" >>"$LOG"
