#!/bin/zsh
# Queue promising local models on audittrap after the in-flight qwen3.6 run.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p results/audittrap
LOG=results/audittrap/local_queue_audittrap.log
: >>"$LOG"
exec >>"$LOG" 2>&1

wait_pid="${1:-}"
if [[ -n "$wait_pid" ]]; then
  echo "==== waiting for pid $wait_pid $(date) ===="
  while kill -0 "$wait_pid" 2>/dev/null; do sleep 15; done
  echo "==== pid $wait_pid gone $(date) ===="
fi

export BENCH_PROVIDER=ollama
export BENCH_THINK=medium
export BENCH_NUM_CTX=65536
export BENCH_NUM_PREDICT=24576
export BENCH_TEMPERATURE=0.1
export BENCH_MAX_ROUNDS=40
export BENCH_MAX_TOOL_CALLS=40
export BENCH_TASK_TIMEOUT_S=1200
export BENCH_THINK_PROMOTE=1
export BENCH_THINK_LOOP=1

run_one() {
  local model="$1" tag="$2"
  echo "==== START $model tag=$tag $(date) ===="
  echo "THINK=$BENCH_THINK CTX=$BENCH_NUM_CTX PREDICT=$BENCH_NUM_PREDICT"
  BENCH_MODEL="$model" BENCH_TAG="$tag" \
    .venv/bin/python -u -m benches.audittrap
  echo "==== DONE $tag $(date) ===="
}

# Promising locals by arch/claim/repohard history (excluding in-flight 3.6).
run_one 'qwen3.5:35b-a3b-coding-bf16' 'qwen3.5_35b-a3b-coding-bf16_audittrap_think'
run_one 'qwen3-coder-next:q8_0' 'qwen3-coder-next_q8_0_audittrap_think'
run_one 'qwen3-coder:30b-a3b-fp16' 'qwen3-coder_30b-a3b-fp16_audittrap_think'

echo "==== local ollama queue DONE $(date); starting ds4 flash ===="
# Free Metal for ds4-server (already listening on :8000).
ollama stop qwen3.5:35b-a3b-coding-bf16 2>/dev/null || true
ollama stop qwen3-coder-next:q8_0 2>/dev/null || true
ollama stop qwen3-coder:30b-a3b-fp16 2>/dev/null || true
ollama stop qwen3.6:35b-a3b-coding-bf16 2>/dev/null || true
sleep 2
export DS4_BASE="${DS4_BASE:-http://127.0.0.1:8000}"
export BENCH_MODEL=deepseek-v4-flash
export BENCH_THINK=0
export BENCH_NUM_CTX=65536
export BENCH_NUM_PREDICT=8192
export BENCH_TASK_TIMEOUT_S=900
export BENCH_TAG_PREFIX=ds4_flash_q2imatrix
.venv/bin/python -u scripts/run_agent_benches_openai.py audittrap

echo "==== local queue ALL DONE $(date) ===="
