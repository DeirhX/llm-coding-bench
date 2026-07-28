#!/bin/zsh
# Fix-ticket patch quality:
#   - qwen3.6: config matrix (universal-matrix / think_budget variants) × BASE vs SYS_gen
#   - other locals: medium/off as before × BASE vs SYS_gen
# Ignores cheaty system_local_v1.
set -euo pipefail
cd "$(dirname "$0")/.."
LOG=results/audittrap/local_sysgen_fixes_queue.log
mkdir -p results/audittrap
exec >>"$LOG" 2>&1

FIX_TASKS='runner_interrupt_scored,chat_timeout_dropped,subprocess_stderr_dropped,warmup_no_deadline'
echo "==== FIX-TICKET patch quality queue (with qwen3.6 config matrix) $(date) ===="
echo "tasks=$FIX_TASKS"

export BENCH_TASKS="$FIX_TASKS"
export BENCH_TEMPERATURE=0.1 BENCH_MAX_ROUNDS=40 BENCH_MAX_TOOL_CALLS=40
export BENCH_TASK_TIMEOUT_S=1200 BENCH_NUM_CTX=65536
export BENCH_KEEP_ALIVE=24h
export BENCH_NUM_PREDICT=24576

echo "==== stop ds4 for ollama $(date) ===="
screen -S ds4_server -X quit 2>/dev/null || true
pkill -f './ds4-server -m ds4flash' 2>/dev/null || true
sleep 2

run_ollama() {
  local model="$1" tag="$2" think="$3" sysfile="$4"
  # optional extra env: max_chars finalize predict
  local max_chars="${5:-0}"
  local finalize="${6:-}"
  local predict="${7:-}"

  echo "==== START $model tag=$tag think=$think sys=$sysfile max_chars=$max_chars finalize=${finalize:--} predict=${predict:-default} $(date) ===="
  export BENCH_PROVIDER=ollama BENCH_THINK="$think"
  export BENCH_THINK_MAX_CHARS="$max_chars"
  if [[ -n "$finalize" ]]; then
    export BENCH_FINALIZE_AFTER="$finalize"
  else
    unset BENCH_FINALIZE_AFTER || true
  fi
  if [[ -n "$predict" ]]; then
    export BENCH_NUM_PREDICT="$predict"
  else
    export BENCH_NUM_PREDICT=24576
  fi
  if [[ "$think" == "0" ]]; then
    export BENCH_THINK_PROMOTE=0 BENCH_THINK_LOOP=0
  else
    export BENCH_THINK_PROMOTE=1 BENCH_THINK_LOOP=1
  fi
  unset BENCH_THINK_ROUNDS DS4_BASE BENCH_TAG_PREFIX BENCH_OPENAI_STREAM || true
  if [[ -z "$sysfile" || "$sysfile" == "0" ]]; then
    export BENCH_SYSTEM_PROMPT=0
    unset BENCH_SYSTEM_PROMPT_FILE || true
  else
    export BENCH_SYSTEM_PROMPT=1
    export BENCH_SYSTEM_PROMPT_FILE="$sysfile"
  fi
  BENCH_MODEL="$model" BENCH_TAG="$tag" .venv/bin/python -u -m benches.audittrap
  echo "==== DONE $tag $(date) ===="
}

GEN=benches/audittrap/system_local_general.md
M36='qwen3.6:35b-a3b-coding-bf16'
P36='qwen3.6_35b-a3b-coding-bf16_audittrap'

echo "==== qwen3.6 config matrix (fix tickets) $(date) ===="
# From scripts/run_qwen36_universal_matrix.sh + think_budget sticky winner
# Each: BASE (no system) then SYS_gen

# 1) think off
run_ollama "$M36" "${P36}_fix_off" '0' '0'
run_ollama "$M36" "${P36}_sysgen_off" '0' "$GEN"

# 2) low uncapped
run_ollama "$M36" "${P36}_fix_low" 'low' '0'
run_ollama "$M36" "${P36}_sysgen_low" 'low' "$GEN"

# 3) medium uncapped (default sticky)
run_ollama "$M36" "${P36}_fix_med" 'medium' '0'
run_ollama "$M36" "${P36}_sysgen_med" 'medium' "$GEN"

# 4) high uncapped
run_ollama "$M36" "${P36}_fix_high" 'high' '0'
run_ollama "$M36" "${P36}_sysgen_high" 'high' "$GEN"

# 5) think_budget winner: medium + 8k char cap
run_ollama "$M36" "${P36}_fix_med_c8k" 'medium' '0' '8192'
run_ollama "$M36" "${P36}_sysgen_med_c8k" 'medium' "$GEN" '8192'

# 6) medium + finalize nudge
run_ollama "$M36" "${P36}_fix_med_fin15" 'medium' '0' '0' '15'
run_ollama "$M36" "${P36}_sysgen_med_fin15" 'medium' "$GEN" '0' '15'

# 7) medium + 16k think cap
run_ollama "$M36" "${P36}_fix_med_c16k" 'medium' '0' '16384'
run_ollama "$M36" "${P36}_sysgen_med_c16k" 'medium' "$GEN" '16384'

echo "==== other locals (medium/off × BASE/GEN) $(date) ===="
run_ollama 'qwen3.5:35b-a3b-coding-bf16' 'qwen3.5_35b-a3b-coding-bf16_audittrap_fixbase' 'medium' '0'
run_ollama 'qwen3.5:35b-a3b-coding-bf16' 'qwen3.5_35b-a3b-coding-bf16_audittrap_sysgen_fixes' 'medium' "$GEN"

run_ollama 'qwen3-coder-next:q8_0' 'qwen3-coder-next_q8_0_audittrap_fixbase' '0' '0'
run_ollama 'qwen3-coder-next:q8_0' 'qwen3-coder-next_q8_0_audittrap_sysgen_fixes' '0' "$GEN"

run_ollama 'qwen3-coder:30b-a3b-fp16' 'qwen3-coder_30b-a3b-fp16_audittrap_fixbase' '0' '0'
run_ollama 'qwen3-coder:30b-a3b-fp16' 'qwen3-coder_30b-a3b-fp16_audittrap_sysgen_fixes' '0' "$GEN"

echo "==== ollama stop before ds4 $(date) ===="
for m in qwen3.6:35b-a3b-coding-bf16 qwen3.5:35b-a3b-coding-bf16 qwen3-coder-next:q8_0 qwen3-coder:30b-a3b-fp16; do
  ollama stop "$m" 2>/dev/null || true
done
sleep 3

echo "==== start ds4-server $(date) ===="
screen -dmS ds4_server zsh -c 'cd /Users/deirh/Projects/ds4 && exec ./ds4-server -m ds4flash.gguf --metal -c 65536 -n 8192 --host 127.0.0.1 --port 8000 2>&1 | tee /Users/deirh/Projects/llm-coding-bench/results/audittrap/ds4_server_sysgen_fixes.nohup.out'
for i in $(seq 1 90); do
  if curl -sf -m 2 http://127.0.0.1:8000/v1/models >/dev/null; then echo "ds4 ready ${i}s"; break; fi
  sleep 2
done
if ! curl -sf -m 2 http://127.0.0.1:8000/v1/models >/dev/null; then
  echo "ds4-server down" >&2
  exit 2
fi

run_ds4() {
  local prefix="$1" sysfile="$2"
  echo "==== START ds4 tag_prefix=$prefix sys=$sysfile $(date) ===="
  unset BENCH_PROVIDER BENCH_OPENAI_BASE_URL BENCH_TAG BENCH_THINK_MAX_CHARS BENCH_FINALIZE_AFTER || true
  export DS4_BASE=http://127.0.0.1:8000
  export BENCH_MODEL=deepseek-v4-flash BENCH_THINK=0
  export BENCH_NUM_PREDICT=2048 BENCH_HTTP_TIMEOUT_S=300
  export BENCH_STREAM_STALL_S=120 BENCH_FIRST_BYTE_S=180 BENCH_OPENAI_STREAM=1
  export BENCH_TAG_PREFIX="$prefix"
  export BENCH_TASKS="$FIX_TASKS"
  if [[ -z "$sysfile" || "$sysfile" == "0" ]]; then
    export BENCH_SYSTEM_PROMPT=0
    unset BENCH_SYSTEM_PROMPT_FILE || true
  else
    export BENCH_SYSTEM_PROMPT=1
    export BENCH_SYSTEM_PROMPT_FILE="$sysfile"
  fi
  .venv/bin/python -u scripts/run_agent_benches_openai.py audittrap
  echo "==== DONE $prefix $(date) ===="
}

run_ds4 'ds4_flash_q2imatrix_fixbase' '0'
run_ds4 'ds4_flash_q2imatrix_sysgen_fixes' "$GEN"

.venv/bin/python - <<'PY'
import json
from pathlib import Path
from collections import Counter

FIXES = [
    "runner_interrupt_scored",
    "chat_timeout_dropped",
    "subprocess_stderr_dropped",
    "warmup_no_deadline",
]

def load(tag):
    p = Path(f"results/audittrap/{tag}_latest.json")
    return json.loads(p.read_text()) if p.exists() else None

def classify(r):
    detail = str(r.get("grade_detail") or "")
    score = int(r.get("score") or 0)
    mx = int(r.get("max_score") or 0)
    ans = r.get("answer") or {}
    patch = str(ans.get("patch") or "") if isinstance(ans, dict) else ""
    status = ans.get("status") if isinstance(ans, dict) else None
    if score >= mx:
        return "ok_full"
    if score > 0 and "pytest" in detail:
        return "partial"
    if "empty patch" in detail:
        return "empty"
    if "status='unchanged'" in detail or status in ("unchanged", "wontfix"):
        return "refused"
    if patch.strip().startswith("{"):
        return "json_wrap"
    if "corrupt patch" in detail:
        return "corrupt"
    if "patch_apply" in detail or "git apply" in detail:
        return "apply_fail"
    if "TIMEOUT" in detail:
        return "timeout"
    return "other"

def summarize(tag):
    d = load(tag)
    if not d:
        return None
    by = {r["task"]: r for r in d}
    pts = 0
    cats = Counter()
    for t in FIXES:
        r = by.get(t)
        if not r:
            continue
        pts += int(r.get("score") or 0)
        cats[classify(r)] += 1
    return pts, cats

print("\n==== qwen3.6 CONFIG MATRIX (fix pts /40) ====")
print(f"{'variant':16} {'BASE':>7} {'GEN':>7}  Δ   cats")
variants = [
    ("off", "fix_off", "sysgen_off"),
    ("low", "fix_low", "sysgen_low"),
    ("med", "fix_med", "sysgen_med"),
    ("high", "fix_high", "sysgen_high"),
    ("med_c8k", "fix_med_c8k", "sysgen_med_c8k"),
    ("med_fin15", "fix_med_fin15", "sysgen_med_fin15"),
    ("med_c16k", "fix_med_c16k", "sysgen_med_c16k"),
]
pfx = "qwen3.6_35b-a3b-coding-bf16_audittrap_"
for name, b, g in variants:
    sb, sg = summarize(pfx + b), summarize(pfx + g)
    if not sb or not sg:
        print(f"{name:16} incomplete base={sb is not None} gen={sg is not None}")
        continue
    bp, bc = sb
    gp, gc = sg
    print(f"{name:16} {bp:2}/40   {gp:2}/40  {gp-bp:+3}  {dict(bc)} → {dict(gc)}")

print("\n==== OTHER LOCALS ====")
others = [
    ("3.5", "qwen3.5_35b-a3b-coding-bf16_audittrap_fixbase", "qwen3.5_35b-a3b-coding-bf16_audittrap_sysgen_fixes"),
    ("next", "qwen3-coder-next_q8_0_audittrap_fixbase", "qwen3-coder-next_q8_0_audittrap_sysgen_fixes"),
    ("30b", "qwen3-coder_30b-a3b-fp16_audittrap_fixbase", "qwen3-coder_30b-a3b-fp16_audittrap_sysgen_fixes"),
    ("ds4", "ds4_flash_q2imatrix_fixbase_audittrap", "ds4_flash_q2imatrix_sysgen_fixes_audittrap"),
]
for name, b, g in others:
    sb, sg = summarize(b), summarize(g)
    if not sb or not sg:
        print(f"{name:5} incomplete")
        continue
    bp, bc = sb
    gp, gc = sg
    print(f"{name:5} BASE {bp:2}/40 {dict(bc)}  GEN {gp:2}/40 {dict(gc)}  Δ{gp-bp:+d}")
print("==== ALL DONE fix-ticket patch quality queue", flush=True)
PY
