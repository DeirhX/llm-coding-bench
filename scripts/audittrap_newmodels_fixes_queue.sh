#!/bin/zsh
# Fix-ticket patch quality for newly added models:
#   gemma4 26b-a4b (MoE), gemma4 31b (dense), Qwopus3.6-35B-A3B-Coder (community Qwen3.6 coder finetune)
# Each: BASE (no system prompt) vs SYS_gen. Qwopus additionally at the qwen3.6 think winners.
# Waits for the in-flight fix queue to finish so the two runs never share the GPU.
set -euo pipefail
cd "$(dirname "$0")/.."
LOG=results/audittrap/local_newmodels_fixes_queue.log
PREV_LOG=results/audittrap/local_sysgen_fixes_queue.log
mkdir -p results/audittrap
exec >>"$LOG" 2>&1

FIX_TASKS='runner_interrupt_scored,chat_timeout_dropped,subprocess_stderr_dropped,warmup_no_deadline'
echo "==== NEW-MODEL fix-ticket queue $(date) ===="
echo "tasks=$FIX_TASKS"

# --- wait for the previous queue to finish (max 6h) ---
if [[ -f "$PREV_LOG" ]]; then
  for i in $(seq 1 2160); do
    if grep -q 'ALL DONE fix-ticket patch quality queue' "$PREV_LOG"; then
      echo "==== previous queue finished, proceeding $(date) ===="
      break
    fi
    sleep 10
  done
  if ! grep -q 'ALL DONE fix-ticket patch quality queue' "$PREV_LOG"; then
    echo "==== previous queue still running after 6h, aborting $(date) ====" >&2
    exit 2
  fi
fi

echo "==== free the GPU: stop ds4 + unload ollama models $(date) ===="
screen -S ds4_server -X quit 2>/dev/null || true
pkill -f './ds4-server -m ds4flash' 2>/dev/null || true
for m in qwen3.6:35b-a3b-coding-bf16 qwen3.5:35b-a3b-coding-bf16 qwen3-coder-next:q8_0 qwen3-coder:30b-a3b-fp16; do
  ollama stop "$m" 2>/dev/null || true
done
sleep 5

export BENCH_TASKS="$FIX_TASKS"
export BENCH_TEMPERATURE=0.1 BENCH_MAX_ROUNDS=40 BENCH_MAX_TOOL_CALLS=40
export BENCH_TASK_TIMEOUT_S=1200 BENCH_NUM_CTX=65536
export BENCH_KEEP_ALIVE=24h
export BENCH_NUM_PREDICT=24576

# `ollama list | grep -q` is a trap under `set -o pipefail`: grep exits on the
# first match, ollama takes SIGPIPE, and the pipeline reports 141 even though the
# model is present. awk drains stdin, so the status is honest.
have_model() {
  ollama list 2>/dev/null | awk -v m="$1" '$1 == m { found = 1 } END { exit found ? 0 : 1 }'
}

run_ollama() {
  local model="$1" tag="$2" think="$3" sysfile="$4"
  local max_chars="${5:-0}"

  # downloads may still be in flight; wait up to 2h for the model to register
  if ! have_model "$model"; then
    echo "==== WAIT for $model to become available $(date) ===="
    for i in $(seq 1 720); do
      if have_model "$model"; then
        echo "==== $model appeared after $((i * 10))s $(date) ===="
        break
      fi
      sleep 10
    done
  fi
  if ! have_model "$model"; then
    echo "==== SKIP $model (not installed after wait) $(date) ====" >&2
    return 0
  fi

  echo "==== START $model tag=$tag think=$think sys=$sysfile max_chars=$max_chars $(date) ===="
  export BENCH_PROVIDER=ollama BENCH_THINK="$think"
  export BENCH_THINK_MAX_CHARS="$max_chars"
  export BENCH_NUM_PREDICT=24576
  unset BENCH_FINALIZE_AFTER || true
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
  BENCH_MODEL="$model" BENCH_TAG="$tag" .venv/bin/python -u -m benches.audittrap || \
    echo "==== FAILED $tag $(date) ====" >&2
  echo "==== DONE $tag $(date) ===="
  ollama stop "$model" 2>/dev/null || true
}

GEN=benches/audittrap/system_local_general.md

# Qwopus runs first: its download completes well before the 51 GB gemma pulls,
# so this order avoids idling the GPU waiting on the slower model.
echo "==== qwopus3.6 35b-a3b-coder (Qwen3.6 finetune) $(date) ===="
QW='qwopus3.6:35b-a3b-coder-q8_0'
QP='qwopus3.6_35b-a3b-coder-q8_0_audittrap'
run_ollama "$QW" "${QP}_fix_off" '0' '0'
run_ollama "$QW" "${QP}_sysgen_off" '0' "$GEN"
run_ollama "$QW" "${QP}_fix_med" 'medium' '0'
run_ollama "$QW" "${QP}_sysgen_med" 'medium' "$GEN"
run_ollama "$QW" "${QP}_fix_high" 'high' '0'
run_ollama "$QW" "${QP}_sysgen_high" 'high' "$GEN"

echo "==== gemma4 26b-a4b (MoE) $(date) ===="
run_ollama 'gemma4:26b-a4b-it-bf16' 'gemma4_26b-a4b-it-bf16_audittrap_fixbase' '0' '0'
run_ollama 'gemma4:26b-a4b-it-bf16' 'gemma4_26b-a4b-it-bf16_audittrap_sysgen_fixes' '0' "$GEN"

echo "==== gemma4 31b (dense) $(date) ===="
run_ollama 'gemma4:31b-it-bf16' 'gemma4_31b-it-bf16_audittrap_fixbase' '0' '0'
run_ollama 'gemma4:31b-it-bf16' 'gemma4_31b-it-bf16_audittrap_sysgen_fixes' '0' "$GEN"

# Gemma 4 ships configurable thinking (<|channel>thought), so don't judge it on
# non-reasoning mode alone. Run these last: primary matrix lands first.
echo "==== gemma4 think=medium pass $(date) ===="
run_ollama 'gemma4:26b-a4b-it-bf16' 'gemma4_26b-a4b-it-bf16_audittrap_fix_med' 'medium' '0'
run_ollama 'gemma4:26b-a4b-it-bf16' 'gemma4_26b-a4b-it-bf16_audittrap_sysgen_med' 'medium' "$GEN"
run_ollama 'gemma4:31b-it-bf16' 'gemma4_31b-it-bf16_audittrap_fix_med' 'medium' '0'
run_ollama 'gemma4:31b-it-bf16' 'gemma4_31b-it-bf16_audittrap_sysgen_med' 'medium' "$GEN"

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
    p = Path(f"results/audittrap/{tag}_latest.json")
    if not p.exists():
        return None
    by = {r["task"]: r for r in json.loads(p.read_text())}
    pts = 0
    cats = Counter()
    for t in FIXES:
        r = by.get(t)
        if not r:
            continue
        pts += int(r.get("score") or 0)
        cats[classify(r)] += 1
    return pts, cats

print("\n==== NEW MODELS (fix pts /40) ====")
pairs = [
    ("gemma4-26b-a4b", "gemma4_26b-a4b-it-bf16_audittrap_fixbase", "gemma4_26b-a4b-it-bf16_audittrap_sysgen_fixes"),
    ("gemma4-31b", "gemma4_31b-it-bf16_audittrap_fixbase", "gemma4_31b-it-bf16_audittrap_sysgen_fixes"),
    ("qwopus off", "qwopus3.6_35b-a3b-coder-q8_0_audittrap_fix_off", "qwopus3.6_35b-a3b-coder-q8_0_audittrap_sysgen_off"),
    ("qwopus med", "qwopus3.6_35b-a3b-coder-q8_0_audittrap_fix_med", "qwopus3.6_35b-a3b-coder-q8_0_audittrap_sysgen_med"),
    ("qwopus high", "qwopus3.6_35b-a3b-coder-q8_0_audittrap_fix_high", "qwopus3.6_35b-a3b-coder-q8_0_audittrap_sysgen_high"),
    ("gemma26 med", "gemma4_26b-a4b-it-bf16_audittrap_fix_med", "gemma4_26b-a4b-it-bf16_audittrap_sysgen_med"),
    ("gemma31 med", "gemma4_31b-it-bf16_audittrap_fix_med", "gemma4_31b-it-bf16_audittrap_sysgen_med"),
]
for name, b, g in pairs:
    sb, sg = summarize(b), summarize(g)
    if not sb and not sg:
        print(f"{name:16} missing")
        continue
    bp, bc = sb if sb else (0, {})
    gp, gc = sg if sg else (0, {})
    print(f"{name:16} BASE {bp:2}/40 {dict(bc)}  GEN {gp:2}/40 {dict(gc)}  d{gp-bp:+d}")
print("==== ALL DONE new-model fix-ticket queue", flush=True)
PY
