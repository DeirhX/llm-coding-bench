#!/usr/bin/env bash
# Pull Gemma 4 candidates (MoE + dense) for audittrap benching.
set -uo pipefail
cd "$(dirname "$0")/.."
for m in gemma4:26b-a4b-it-bf16 gemma4:31b-it-bf16; do
  echo "==== PULL $m $(date) ===="
  for attempt in 1 2 3; do
    if ollama pull "$m"; then
      echo "==== DONE $m $(date) ===="
      break
    fi
    echo "==== RETRY $attempt failed for $m $(date) ===="
    sleep 10
  done
done
echo "==== ALL DONE gemma4 pull $(date) ===="
ollama list | grep -i gemma4 || true
