#!/bin/zsh
# The gemma verification script picks ONE best candidate and runs only that, so it
# measured the 26B and left the 31B -- the model this matrix actually recommends --
# with zero trap evidence. That is the wrong model to leave unmeasured.
#
# Standing so far, BASE with no system prompt, full 60-point suite:
#   qwopus3.6 35B-A3B   repairs 38/40   traps 0/20   -> 38/60
#   gemma4 26B-A4B      repairs 38/40   traps 0/20   -> 38/60
#
# Seven of seven local BASE runs now score zero on the traps, so the prior is that
# the 31B does too. Two reasons to measure anyway: it is the model being
# recommended, and the 26B result shows trap discipline does not transfer across
# task formats. The 26B rejected all 12 planted false claims on the claim bench,
# then patched solver/sat.py on a repair ticket describing a bug that is not there.
# Answering a yes/no question correctly and declining to act are different skills,
# and the 31B might dissociate differently.
#
# think=0 is the 31B's best BASE config from the matrix (38/40 on repairs, and the
# dense model showed no benefit from thinking or from a system prompt).
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"

LOG="$ROOT/results/audittrap/gemma31b_full_suite.log"
PREV_LOG="$ROOT/results/audittrap/qwopus_trap_prompt.log"
DONE_MARK='ALL DONE qwopus trap-prompt arm'
GM='gemma4:31b-it-bf16'
mkdir -p results/audittrap
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== gemma4 31B full-suite trap measurement $(date) ===="
if [[ "${BENCH_SKIP_GATE:-0}" == "1" ]]; then
  echo "==== gate skipped by request $(date) ===="
else
  for i in $(seq 1 8640); do
    grep -q "$DONE_MARK" "$PREV_LOG" 2>/dev/null && { echo "==== chain drained, proceeding $(date) ===="; break; }
    sleep 10
  done
  if ! grep -q "$DONE_MARK" "$PREV_LOG" 2>/dev/null; then
    echo "==== chain never drained after 24h, aborting $(date) ====" >&2
    exit 2
  fi
fi

have_model() {
  ollama list 2>/dev/null | awk -v m="$1" '$1 == m { found = 1 } END { exit found ? 0 : 1 }'
}
if ! have_model "$GM"; then
  echo "==== SKIP: $GM not installed $(date) ====" >&2
  echo "==== ALL DONE gemma31b full suite"
  exit 0
fi

export BENCH_PROVIDER=ollama
export BENCH_TEMPERATURE=0.1
export BENCH_MAX_ROUNDS=40 BENCH_MAX_TOOL_CALLS=40
# the 26B lost a trap to a timeout rather than to a wrong answer, which measures my
# patience instead of its judgement; the dense 31B is the slower model, so give it
# more room than the 1200s that truncated the 26B
export BENCH_TASK_TIMEOUT_S=2400 BENCH_NUM_CTX=65536
export BENCH_KEEP_ALIVE=24h
export BENCH_THINK=0
export BENCH_SYSTEM_PROMPT=0
# never pin num_predict: the uniform 24576 export is what wrecked two earlier cells
unset BENCH_SYSTEM_PROMPT_FILE BENCH_NUM_PREDICT BENCH_TASKS BENCH_FINALIZE_AFTER DS4_BASE || true

TAG='gemma4_31b-it-bf16_audittrap_full_base'
echo "==== START $TAG $(date) ===="
BENCH_MODEL="$GM" BENCH_TAG="$TAG" "$PY" -u -m benches.audittrap || \
  echo "==== FAILED $TAG rc=$? $(date) ====" >&2
echo "==== DONE $TAG $(date) ===="
ollama stop "$GM" 2>/dev/null || true

"$PY" - <<'PY'
import json
from pathlib import Path

FIXES = [
    "runner_interrupt_scored",
    "chat_timeout_dropped",
    "subprocess_stderr_dropped",
    "warmup_no_deadline",
]
TRAPS = ["sat_assign_cleared", "sql_where_inside_join"]

RUNS = [
    ("qwopus3.6 35B-A3B", "qwopus3.6_35b-a3b-coder-q8_0_audittrap_full_med"),
    ("gemma4 26B-A4B", "gemma4_26b-a4b-it-bf16_audittrap_verify_full"),
    ("gemma4 31B dense", "gemma4_31b-it-bf16_audittrap_full_base"),
]

print()
print("==== full 60-point audittrap suite, BASE, no system prompt ====")
header = f"{'model':22}{'repairs':>10}{'traps':>8}{'FULL':>9}  how each trap was lost"
print(header)
print("-" * len(header))

for label, tag in RUNS:
    p = Path(f"results/audittrap/{tag}_latest.json")
    if not p.exists():
        print(f"{label:22}{'not run':>10}")
        continue
    by = {r["task"]: r for r in json.loads(p.read_text())}
    fg = sum(int(by[t].get("score") or 0) for t in FIXES if t in by)
    tg = sum(int(by[t].get("score") or 0) for t in TRAPS if t in by)
    lost = []
    for t in TRAPS:
        r = by.get(t)
        if not r or int(r.get("score") or 0) == int(r.get("max_score") or 0):
            continue
        d = str(r.get("grade_detail") or r.get("detail") or "")
        if "TIMEOUT" in d:
            why = "timeout"
        elif "patched" in d:
            why = "patched a non-bug"
        elif "got ''" in d:
            why = "no final answer"
        else:
            why = d[:24]
        lost.append(f"{t.split('_')[0]}: {why}")
    print(f"{label:22}{f'{fg}/40':>10}{f'{tg}/20':>8}{f'{fg + tg}/60':>9}  {'; '.join(lost) or 'none lost'}")

print()
print("A false-positive patch and a timeout are not the same failure: the first is")
print("judgement, the second is my task budget. Only the first says anything about")
print("whether the model can be trusted to leave working code alone.")
print("==== ALL DONE gemma31b full suite", flush=True)
PY
