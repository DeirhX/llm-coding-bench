#!/bin/zsh
# Qwopus BASE think=medium scored a perfect 40/40 on the four repair tickets.
# Two reasons that is not yet a result:
#   1. n=1. ds4 swung 38 -> 28 on identical inputs, so repeat it.
#   2. The fix-only queue never showed it the two planted false bugs. A model that
#      patches everything aces repairs and fails the traps; that is the exact
#      inverse of the GPT-5.6 refusal pathology and scores identically here.
# Chained behind the budget probe so the three scripts never share the GPU.
set -euo pipefail
cd "$(dirname "$0")/.."
LOG=results/audittrap/qwopus_verify.log
PREV_LOG=results/audittrap/qwopus_budget_probe.log
DONE_MARK='ALL DONE qwopus budget probe'
mkdir -p results/audittrap
exec >>"$LOG" 2>&1

echo "==== QWOPUS verification $(date) ===="
for i in $(seq 1 2880); do
  if grep -q "$DONE_MARK" "$PREV_LOG" 2>/dev/null; then
    echo "==== budget probe finished, proceeding $(date) ===="
    break
  fi
  sleep 10
done
if ! grep -q "$DONE_MARK" "$PREV_LOG" 2>/dev/null; then
  echo "==== budget probe still running after 8h, aborting $(date) ====" >&2
  exit 2
fi

have_model() {
  ollama list 2>/dev/null | awk -v m="$1" '$1 == m { found = 1 } END { exit found ? 0 : 1 }'
}

QW='qwopus3.6:35b-a3b-coder-q8_0'
if ! have_model "$QW"; then
  echo "==== SKIP: $QW not installed $(date) ====" >&2
  exit 3
fi

# Shared settings: identical to the matrix run that scored 40/40, so any
# difference in outcome is variance or the traps, not configuration drift.
export BENCH_PROVIDER=ollama
export BENCH_TEMPERATURE=0.1 BENCH_MAX_ROUNDS=40 BENCH_MAX_TOOL_CALLS=40
export BENCH_TASK_TIMEOUT_S=1200 BENCH_NUM_CTX=65536
export BENCH_KEEP_ALIVE=24h BENCH_NUM_PREDICT=24576
export BENCH_THINK=medium BENCH_THINK_MAX_CHARS=0
export BENCH_THINK_PROMOTE=1 BENCH_THINK_LOOP=1
export BENCH_SYSTEM_PROMPT=0
unset BENCH_SYSTEM_PROMPT_FILE BENCH_FINALIZE_AFTER BENCH_THINK_ROUNDS DS4_BASE || true

FIX_TASKS='runner_interrupt_scored,chat_timeout_dropped,subprocess_stderr_dropped,warmup_no_deadline'

run() {
  local tag="$1"
  echo "==== START $tag $(date) ===="
  BENCH_MODEL="$QW" BENCH_TAG="$tag" .venv/bin/python -u -m benches.audittrap || \
    echo "==== FAILED $tag $(date) ====" >&2
  echo "==== DONE $tag $(date) ===="
  ollama stop "$QW" 2>/dev/null || true
}

# 1) Full suite: real repairs AND the false-bug traps. The headline test.
unset BENCH_TASKS || true
run 'qwopus3.6_35b-a3b-coder-q8_0_audittrap_full_med'

# 2) Reproducibility of the 40/40, same four tickets, same everything.
export BENCH_TASKS="$FIX_TASKS"
run 'qwopus3.6_35b-a3b-coder-q8_0_audittrap_fix_med_rep2'
run 'qwopus3.6_35b-a3b-coder-q8_0_audittrap_fix_med_rep3'

.venv/bin/python - <<'PY'
import json
from pathlib import Path

FIXES = [
    "runner_interrupt_scored",
    "chat_timeout_dropped",
    "subprocess_stderr_dropped",
    "warmup_no_deadline",
]
TRAPS = ["sat_assign_cleared", "sql_where_inside_join"]
BASE = "qwopus3.6_35b-a3b-coder-q8_0_audittrap_"

def load(tag):
    p = Path(f"results/audittrap/{tag}_latest.json")
    if not p.exists():
        return None
    return {r["task"]: r for r in json.loads(p.read_text())}

def pts(by, tasks):
    got = sum(int(by[t].get("score") or 0) for t in tasks if t in by)
    mx = sum(int(by[t].get("max_score") or 0) for t in tasks if t in by)
    return got, mx

print("\n==== Qwopus think=medium, BASE: is the 40/40 real? ====")
for label, tag in [
    ("matrix (original)", "fix_med"),
    ("repeat 2", "fix_med_rep2"),
    ("repeat 3", "fix_med_rep3"),
    ("full suite", "full_med"),
]:
    by = load(BASE + tag)
    if not by:
        print(f"{label:20} missing")
        continue
    fg, fm = pts(by, FIXES)
    tg, tm = pts(by, TRAPS)
    trap = f"traps {tg}/{tm}" if tm else "traps not run"
    capped = sum(1 for t in FIXES if t in by and int(by[t].get("rounds") or 0) >= 40)
    print(f"{label:20} fixes {fg:2}/{fm:2}  {trap:16} {capped} capped")

print("\nA high fix score with a low trap score means it patches whatever it is")
print("shown, not that it reasons well. Both halves have to hold.")
print("==== ALL DONE qwopus verification", flush=True)
PY
