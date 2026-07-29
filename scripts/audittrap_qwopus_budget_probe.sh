#!/bin/zsh
# Diagnostic: does Qwopus fail to *fix* the tickets, or merely fail to *stop*?
# The 14-run matrix caps the agent loop at 40 rounds, matching every earlier run
# on the scoreboard. Qwopus hit that ceiling instead of submitting, so re-run
# only the configs that capped out, this time at 120 rounds. Tagged _r120 so it
# can never be mistaken for a comparable matrix entry.
#
# BASE ONLY: the sysgen_* arms are gone. We are hunting settings usable for real
# coding, and the system prompt is not part of that picture -- on gemma4 26B it
# cost 8 points and multiplied output by 10.7x (641 -> 6876 tokens/task), while
# leaving the dense 31B unmoved. Dropping the three sysgen configs also halves
# this probe's GPU time, which matters at 120 rounds and a 2400s task ceiling.
set -euo pipefail
cd "$(dirname "$0")/.."
LOG=results/audittrap/qwopus_budget_probe.log
PREV_LOG=results/newmodels_other_benches.log
DONE_MARK='ALL DONE new-model other-bench queue'
mkdir -p results/audittrap
exec >>"$LOG" 2>&1

FIX_TASKS='runner_interrupt_scored,chat_timeout_dropped,subprocess_stderr_dropped,warmup_no_deadline'
echo "==== QWOPUS budget probe (BASE only) $(date) ===="

# wait up to 20h for the other-bench sweep to finish; never share the GPU with it
for i in $(seq 1 7200); do
  if grep -q "$DONE_MARK" "$PREV_LOG" 2>/dev/null; then
    echo "==== other-bench sweep finished, proceeding $(date) ===="
    break
  fi
  sleep 10
done
if ! grep -q "$DONE_MARK" "$PREV_LOG" 2>/dev/null; then
  echo "==== other-bench sweep still running after 20h, aborting $(date) ====" >&2
  exit 2
fi

# `ollama list | grep -q` is a trap under pipefail: grep exits on first match,
# ollama takes SIGPIPE, pipeline reports 141 even when the model is present.
have_model() {
  ollama list 2>/dev/null | awk -v m="$1" '$1 == m { found = 1 } END { exit found ? 0 : 1 }'
}

# Which BASE matrix configs actually hit the ceiling? Only those are worth re-running.
.venv/bin/python - <<'PY' > /tmp/qwopus_capped.txt
import json
from pathlib import Path

CAP = 40
CONFIGS = ["fix_off", "fix_med", "fix_high"]
for cfg in CONFIGS:
    tag = f"qwopus3.6_35b-a3b-coder-q8_0_audittrap_{cfg}"
    p = Path(f"results/audittrap/{tag}_latest.json")
    if not p.exists():
        continue
    rows = json.loads(p.read_text())
    capped = [r for r in rows if int(r.get("rounds") or 0) >= CAP]
    if capped:
        print(f"{cfg}\t{len(capped)}")
PY

if [[ ! -s /tmp/qwopus_capped.txt ]]; then
  echo "==== no BASE config hit the 40-round ceiling, probe unnecessary $(date) ===="
  echo "==== ALL DONE qwopus budget probe"
  exit 0
fi
echo "==== configs that capped out (config, #tickets at ceiling): ===="
cat /tmp/qwopus_capped.txt

export BENCH_TASKS="$FIX_TASKS"
export BENCH_TEMPERATURE=0.1
export BENCH_MAX_ROUNDS=120 BENCH_MAX_TOOL_CALLS=120
export BENCH_TASK_TIMEOUT_S=2400 BENCH_NUM_CTX=65536
export BENCH_KEEP_ALIVE=24h
export BENCH_NUM_PREDICT=24576
export BENCH_PROVIDER=ollama

# No system prompt anywhere in this probe.
export BENCH_SYSTEM_PROMPT=0
unset BENCH_SYSTEM_PROMPT_FILE || true

QW='qwopus3.6:35b-a3b-coder-q8_0'

if ! have_model "$QW"; then
  echo "==== SKIP: $QW not installed $(date) ====" >&2
  exit 3
fi

while IFS=$'\t' read -r cfg _n; do
  [[ -z "$cfg" ]] && continue
  case "$cfg" in
    *_off)  think=0 ;;
    *_med)  think=medium ;;
    *_high) think=high ;;
  esac
  if [[ "$think" == "0" ]]; then
    export BENCH_THINK_PROMOTE=0 BENCH_THINK_LOOP=0
  else
    export BENCH_THINK_PROMOTE=1 BENCH_THINK_LOOP=1
  fi
  export BENCH_THINK="$think" BENCH_THINK_MAX_CHARS=0
  tag="qwopus3.6_35b-a3b-coder-q8_0_audittrap_${cfg}_r120"
  echo "==== START $tag rounds=120 think=$think sysprompt=off $(date) ===="
  BENCH_MODEL="$QW" BENCH_TAG="$tag" .venv/bin/python -u -m benches.audittrap || \
    echo "==== FAILED $tag $(date) ====" >&2
  echo "==== DONE $tag $(date) ===="
  ollama stop "$QW" 2>/dev/null || true
done < /tmp/qwopus_capped.txt

.venv/bin/python - <<'PY'
import json
from pathlib import Path

FIXES = [
    "runner_interrupt_scored",
    "chat_timeout_dropped",
    "subprocess_stderr_dropped",
    "warmup_no_deadline",
]
BASE = "qwopus3.6_35b-a3b-coder-q8_0_audittrap_"

def load(tag):
    p = Path(f"results/audittrap/{tag}_latest.json")
    if not p.exists():
        return None
    return {r["task"]: r for r in json.loads(p.read_text())}

print("\n==== 40 rounds vs 120 rounds: did more budget buy patches? (BASE only) ====")
for cfg in ["fix_off", "fix_med", "fix_high"]:
    a, b = load(BASE + cfg), load(BASE + cfg + "_r120")
    if not a or not b:
        continue
    ap = sum(int(a[t].get("score") or 0) for t in FIXES if t in a)
    bp = sum(int(b[t].get("score") or 0) for t in FIXES if t in b)
    acap = sum(1 for t in FIXES if t in a and int(a[t].get("rounds") or 0) >= 40)
    bcap = sum(1 for t in FIXES if t in b and int(b[t].get("rounds") or 0) >= 120)
    print(f"{cfg:12} 40r {ap:2}/40 ({acap} capped)  ->  120r {bp:2}/40 ({bcap} capped)  d{bp - ap:+d}")
print("==== ALL DONE qwopus budget probe", flush=True)
PY
