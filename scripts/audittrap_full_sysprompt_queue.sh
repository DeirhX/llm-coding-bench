#!/bin/zsh
set -euo pipefail
cd /Users/deirh/Projects/llm-coding-bench
LOG=results/audittrap/local_sysprompt_full_queue.log
mkdir -p results/audittrap
exec >>"$LOG" 2>&1
echo "==== FULL audittrap + system_local.md queue $(date) ===="

export BENCH_SYSTEM_PROMPT=1
export BENCH_SYSTEM_PROMPT_FILE=benches/audittrap/system_local.md
unset BENCH_TASKS || true
export BENCH_TEMPERATURE=0.1 BENCH_MAX_ROUNDS=40 BENCH_MAX_TOOL_CALLS=40
export BENCH_TASK_TIMEOUT_S=1200 BENCH_NUM_CTX=65536
export BENCH_KEEP_ALIVE=24h

echo "==== stop ds4 for ollama $(date) ===="
screen -S ds4_server -X quit 2>/dev/null || true
pkill -f './ds4-server -m ds4flash' 2>/dev/null || true
sleep 3

run_ollama() {
  local model="$1" tag="$2" think="$3"
  echo "==== START $model tag=$tag think=$think $(date) ===="
  export BENCH_PROVIDER=ollama BENCH_THINK="$think" BENCH_NUM_PREDICT=24576
  if [[ "$think" == "0" ]]; then
    export BENCH_THINK_PROMOTE=0 BENCH_THINK_LOOP=0
  else
    export BENCH_THINK_PROMOTE=1 BENCH_THINK_LOOP=1
  fi
  unset DS4_BASE BENCH_TAG_PREFIX BENCH_OPENAI_STREAM || true
  BENCH_MODEL="$model" BENCH_TAG="$tag" .venv/bin/python -u -m benches.audittrap
  echo "==== DONE $tag $(date) ===="
}

run_ollama 'qwen3.6:35b-a3b-coding-bf16' 'qwen3.6_35b-a3b-coding-bf16_audittrap_sysprompt_think' 'medium'
run_ollama 'qwen3.5:35b-a3b-coding-bf16' 'qwen3.5_35b-a3b-coding-bf16_audittrap_sysprompt_think' 'medium'
run_ollama 'qwen3-coder-next:q8_0' 'qwen3-coder-next_q8_0_audittrap_sysprompt' '0'
run_ollama 'qwen3-coder:30b-a3b-fp16' 'qwen3-coder_30b-a3b-fp16_audittrap_sysprompt' '0'

echo "==== ollama stop before ds4 $(date) ===="
for m in qwen3.6:35b-a3b-coding-bf16 qwen3.5:35b-a3b-coding-bf16 qwen3-coder-next:q8_0 qwen3-coder:30b-a3b-fp16; do
  ollama stop "$m" 2>/dev/null || true
done
sleep 3

echo "==== start ds4-server $(date) ===="
screen -dmS ds4_server zsh -c 'cd /Users/deirh/Projects/ds4 && exec ./ds4-server -m ds4flash.gguf --metal -c 65536 -n 8192 --host 127.0.0.1 --port 8000 2>&1 | tee /Users/deirh/Projects/llm-coding-bench/results/audittrap/ds4_server_sysprompt_full.nohup.out'
for i in $(seq 1 90); do
  if curl -sf -m 2 http://127.0.0.1:8000/v1/models >/dev/null; then echo "ds4 ready ${i}s"; break; fi
  sleep 2
done
if ! curl -sf http://127.0.0.1:8000/v1/models >/dev/null; then
  echo "ds4-server down" >&2
  exit 2
fi

echo "==== START ds4 flash full audittrap $(date) ===="
export DS4_BASE=http://127.0.0.1:8000
export BENCH_MODEL=deepseek-v4-flash BENCH_THINK=0
export BENCH_NUM_PREDICT=2048 BENCH_HTTP_TIMEOUT_S=300
export BENCH_STREAM_STALL_S=120 BENCH_FIRST_BYTE_S=180 BENCH_OPENAI_STREAM=1
export BENCH_TAG_PREFIX=ds4_flash_q2imatrix_sysprompt
.venv/bin/python -u scripts/run_agent_benches_openai.py audittrap
echo "==== DONE ds4_flash_q2imatrix_sysprompt_audittrap $(date) ===="

.venv/bin/python - <<'PY'
import json
from pathlib import Path
print('\n==== FULL SYSPROMPT BOARD ====')
tags=[
 'qwen3.6_35b-a3b-coding-bf16_audittrap_sysprompt_think',
 'qwen3.5_35b-a3b-coding-bf16_audittrap_sysprompt_think',
 'qwen3-coder-next_q8_0_audittrap_sysprompt',
 'qwen3-coder_30b-a3b-fp16_audittrap_sysprompt',
 'ds4_flash_q2imatrix_sysprompt_audittrap',
]
old={
 'qwen3.6_35b-a3b-coding-bf16_audittrap_sysprompt_think':'qwen3.6_35b-a3b-coding-bf16_audittrap_think',
 'qwen3.5_35b-a3b-coding-bf16_audittrap_sysprompt_think':'qwen3.5_35b-a3b-coding-bf16_audittrap_think',
 'qwen3-coder-next_q8_0_audittrap_sysprompt':'qwen3-coder-next_q8_0_audittrap',
 'qwen3-coder_30b-a3b-fp16_audittrap_sysprompt':'qwen3-coder_30b-a3b-fp16_audittrap',
 'ds4_flash_q2imatrix_sysprompt_audittrap':'ds4_flash_q2imatrix_audittrap',
}
for tag in tags:
    p=Path(f'results/audittrap/{tag}_latest.json')
    if not p.exists():
        print(f'{tag}: MISSING')
        continue
    d=json.loads(p.read_text())
    score=sum(int(r.get('score')or 0) for r in d)
    mx=sum(int(r.get('max_score')or 0) for r in d)
    base=old.get(tag)
    bscore='—'
    bp=Path(f'results/audittrap/{base}_latest.json') if base else None
    if bp and bp.exists():
        bd=json.loads(bp.read_text())
        bscore=sum(int(r.get('score')or 0) for r in bd)
    print(f'{score:3}/{mx} (was {bscore})  {tag}')
    for r in d:
        print(f"  {r['task']:28} {r.get('score')}/{r.get('max_score')}  {str(r.get('grade_detail'))[:60]}")
PY
echo "==== ALL FULL SYSPROMPT DONE $(date) ===="
