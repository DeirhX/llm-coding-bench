#!/bin/zsh
# After the full sysprompt queue frees the GPU, run trap-only A/B with the
# general (non-bench-specific) system prompt. Compare vs BASE and v1 sysprompt.
set -euo pipefail
cd "$(dirname "$0")/.."
LOG=results/audittrap/local_sysprompt_general_traps_queue.log
mkdir -p results/audittrap
exec >>"$LOG" 2>&1

echo "==== WAIT for audittrap_sysprompt_full to finish $(date) ===="
while screen -ls 2>/dev/null | grep -q 'audittrap_sysprompt_full'; do
  sleep 60
done
# also wait if a leftover benches.audittrap is still running
while pgrep -f 'benches.audittrap|run_agent_benches_openai.py audittrap' >/dev/null 2>&1; do
  echo "…. still see audittrap python $(date)"
  sleep 30
done

echo "==== TRAPS + system_local_general.md queue $(date) ===="
pkill -f 'ds4-server' 2>/dev/null || true
sleep 2

export BENCH_SYSTEM_PROMPT=1
export BENCH_SYSTEM_PROMPT_FILE=benches/audittrap/system_local_general.md
export BENCH_TASKS='sat_assign_cleared,sql_where_inside_join'
export BENCH_TASK_TIMEOUT_S="${BENCH_TASK_TIMEOUT_S:-900}"
export BENCH_KEEP_ALIVE="${BENCH_KEEP_ALIVE:-24h}"

run_ollama() {
  local model="$1" tag="$2" think="$3"
  echo "==== START $model tag=$tag think=$think $(date) ===="
  export BENCH_PROVIDER=ollama
  export BENCH_MODEL="$model"
  export BENCH_TAG="$tag"
  export BENCH_THINK="$think"
  unset BENCH_TAG_PREFIX || true
  .venv/bin/python -u -m benches.audittrap
  echo "==== DONE $tag $(date) ===="
}

run_ollama 'qwen3-coder-next:q8_0' 'qwen3-coder-next_q8_0_audittrap_sysgen_traps' '0'
run_ollama 'qwen3-coder:30b-a3b-fp16' 'qwen3-coder_30b-a3b-fp16_audittrap_sysgen_traps' '0'
run_ollama 'qwen3.5:35b-a3b-coding-bf16' 'qwen3.5_35b-a3b-coding-bf16_audittrap_sysgen_traps' 'medium'
run_ollama 'qwen3.6:35b-a3b-coding-bf16' 'qwen3.6_35b-a3b-coding-bf16_audittrap_sysgen_traps' 'medium'

echo "==== START ds4 flash traps + general prompt $(date) ===="
screen -dmS ds4_server zsh -c 'cd /Users/deirh/Projects/ds4 && exec ./ds4-server -m ds4flash.gguf --metal -c 65536 -n 8192 --host 127.0.0.1 --port 8000 2>&1 | tee /Users/deirh/Projects/llm-coding-bench/results/audittrap/ds4_server_sysgen_traps.nohup.out'
for i in $(seq 1 60); do
  if curl -sf http://127.0.0.1:8000/v1/models >/dev/null 2>&1; then break; fi
  sleep 2
done
# audittrap only accepts ollama|cursor; run_agent_benches_openai patches chat().
# Do NOT set BENCH_PROVIDER=openai (SystemExit after warmup).
unset BENCH_PROVIDER BENCH_OPENAI_BASE_URL BENCH_TAG || true
export DS4_BASE=http://127.0.0.1:8000
export BENCH_MODEL=deepseek-v4-flash
export BENCH_THINK=0
export BENCH_NUM_PREDICT=2048 BENCH_HTTP_TIMEOUT_S=300
export BENCH_STREAM_STALL_S=120 BENCH_FIRST_BYTE_S=180 BENCH_OPENAI_STREAM=1
export BENCH_TAG_PREFIX=ds4_flash_q2imatrix_sysgen_traps
.venv/bin/python -u scripts/run_agent_benches_openai.py audittrap
echo "==== DONE ds4_flash_q2imatrix_sysgen_traps_audittrap $(date) ===="

.venv/bin/python - <<'PY'
import json
from pathlib import Path

traps = ["sat_assign_cleared", "sql_where_inside_join"]
series = [
    ("BASE", {
        "next": "qwen3-coder-next_q8_0_audittrap",
        "30b": "qwen3-coder_30b-a3b-fp16_audittrap",
        "3.5": "qwen3.5_35b-a3b-coding-bf16_audittrap_think",
        "3.6": "qwen3.6_35b-a3b-coding-bf16_audittrap_think",
        "ds4": "ds4_flash_q2imatrix_audittrap",
    }),
    ("SYS_v1", {
        "next": "qwen3-coder-next_q8_0_audittrap_sysprompt_traps",
        "30b": "qwen3-coder_30b-a3b-fp16_audittrap_sysprompt_traps",
        "3.5": "qwen3.5_35b-a3b-coding-bf16_audittrap_sysprompt_think",  # full run may have traps
        "3.6": "qwen3.6_35b-a3b-coding-bf16_audittrap_sysprompt_think",
        "ds4": "ds4_flash_q2imatrix_sysprompt_traps_audittrap",
    }),
    ("SYS_gen", {
        "next": "qwen3-coder-next_q8_0_audittrap_sysgen_traps",
        "30b": "qwen3-coder_30b-a3b-fp16_audittrap_sysgen_traps",
        "3.5": "qwen3.5_35b-a3b-coding-bf16_audittrap_sysgen_traps",
        "3.6": "qwen3.6_35b-a3b-coding-bf16_audittrap_sysgen_traps",
        "ds4": "ds4_flash_q2imatrix_sysgen_traps_audittrap",
    }),
]

def trap_score(tag: str) -> tuple[int, int, str]:
    p = Path(f"results/audittrap/{tag}_latest.json")
    if not p.exists():
        return 0, 20, "missing"
    d = json.loads(p.read_text())
    by = {r["task"]: r for r in d}
    s = mx = 0
    bits = []
    for t in traps:
        r = by.get(t)
        if not r:
            bits.append(f"{t}=?")
            continue
        sc, m = int(r.get("score") or 0), int(r.get("max_score") or 0)
        s += sc
        mx += m
        bits.append(f"{t[:8]}={sc}/{m}")
    return s, mx or 20, " ".join(bits)

print("==== TRAP COMPARISON BASE / SYS_v1 / SYS_gen ====")
models = ["next", "30b", "3.5", "3.6", "ds4"]
hdr = f"{'model':6}  " + "  ".join(f"{name:10}" for name, _ in series)
print(hdr)
for m in models:
    cells = []
    for name, mapping in series:
        tag = mapping[m]
        s, mx, detail = trap_score(tag)
        cells.append(f"{s:2}/{mx:<2}".ljust(10))
    print(f"{m:6}  " + "  ".join(cells))
    for name, mapping in series:
        s, mx, detail = trap_score(mapping[m])
        print(f"         {name:8} {mapping[m]} :: {detail}")
print("==== ALL DONE general-prompt trap queue", flush=True)
PY
