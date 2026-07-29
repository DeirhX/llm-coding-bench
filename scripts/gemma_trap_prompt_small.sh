#!/bin/zsh
# Can a *small, generic* system prompt fix trap discipline?
#
# Both gemmas score 0/20 on the false-bug traps with no system prompt. The two traps are
# confidently worded reports that are simply wrong about the live code, and the protocol
# the user turn already offers {"status": "unchanged"} as a legal final answer. So the
# mechanism is present; what is missing is the disposition to use it.
#
# The prompt this repo already has (system_local.md, 341 words) supplies both, but it is
# bench furniture: it names the "unchanged" status, warns about miniharness/ path
# prefixes, and coaches @@ hunk headers. Nobody would paste that into a real editor, and
# a trap score obtained with it does not tell you how the model behaves in your own
# setup. The two prompts here mention neither this bench nor its vocabulary.
#
#   skeptic_min (63 words)      pure epistemics: reports are hearsay, read the source,
#                               source wins. Says nothing about making no change.
#   skeptic_nochange (107 w)    the same, plus explicit licence to conclude nothing is
#                               wrong and a warning that a speculative fix is a
#                               regression you introduced.
#
# The gap between them isolates the interesting question: is skepticism alone enough once
# the protocol offers a no-change option, or does the model also need to be told that
# answering "nothing is broken" is an acceptable thing to do?
#
# THE FULL SUITE RUNS, NOT JUST THE TRAPS. A prompt that wins 20 trap points by making
# the model refuse the four real repairs is a worse model, not a better one, and the
# claim battery is where over-suspicion would show up first as false negatives. Scoring
# only the traps would manufacture a success. Every arm reports all three families.
#
# Conditions are identical to the system_local.md arms running immediately before this,
# so prompt is the only variable: tuned models, rescues off, Modelfile sampler.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"

LOG="$ROOT/results/gemma_trap_prompt_small.log"
PREV_LOG="$ROOT/results/gemma_interactive_realism.log"
DONE_MARK='ALL DONE gemma interactive realism'
mkdir -p results
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== small generic trap prompts $(date) ===="
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

run_arm() {
  local model="$1" tag="$2" sysp="$3" note="$4"
  if ! have_model "$model"; then
    echo "---- SKIP $tag: $model missing ----" >&2
    return 0
  fi
  echo
  echo "---- START $tag model=$model prompt=$sysp $(date) ----"
  echo "     $note"
  (
    export BENCH_PROVIDER=ollama
    export BENCH_REALISM=1
    export BENCH_TEMPERATURE=auto
    export BENCH_THINK=0
    export BENCH_MAX_ROUNDS=40 BENCH_MAX_TOOL_CALLS=40
    export BENCH_TASK_TIMEOUT_S=1200 BENCH_NUM_CTX=65536
    export BENCH_KEEP_ALIVE=24h
    unset BENCH_NUM_PREDICT BENCH_FINALIZE_AFTER BENCH_THINK_MAX_CHARS || true
    unset BENCH_THINK_LOOP BENCH_THINK_PROMOTE BENCH_STOP_FABRICATION || true
    export BENCH_SYSTEM_PROMPT=1 BENCH_SYSTEM_PROMPT_FILE="$sysp"
    BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" -u -m benches.audittrap
  ) || echo "---- FAILED $tag rc=$? $(date) ----" >&2
  echo "---- DONE $tag $(date) ----"
  ollama stop "$model" 2>/dev/null || true
}

MIN='prompts/skeptic_min.md'
NOC='prompts/skeptic_nochange.md'

run_arm 'gemma4-coding:31b'     'gemma4-coding_31b_audittrap_skeptic_min'          "$MIN" \
  'is 63 words of "reports are hearsay" enough, with no mention of leaving code alone?'
run_arm 'gemma4-coding:31b'     'gemma4-coding_31b_audittrap_skeptic_nochange'     "$NOC" \
  'same plus explicit licence to conclude nothing is wrong'
run_arm 'gemma4-coding:26b-a4b' 'gemma4-coding_26b-a4b_audittrap_skeptic_min'      "$MIN" \
  'the sparse model, which was the more suggestible of the two under system prompts'
run_arm 'gemma4-coding:26b-a4b' 'gemma4-coding_26b-a4b_audittrap_skeptic_nochange' "$NOC" \
  'same plus the licence'

"$PY" - <<'PY'
import json
from pathlib import Path

TRAPS = {"sat_assign_cleared", "sql_where_inside_join"}
FIXES = {"runner_interrupt_scored", "chat_timeout_dropped",
         "subprocess_stderr_dropped", "warmup_no_deadline"}

# The two controls used the stock models with harness rescues on, not BENCH_REALISM=1,
# so they are the right baseline for "what does the prompt change" and the wrong one for
# "is this deployable". Both landed 16/21 claims, 38/40 fixes, 0/20 traps -- an identical
# profile from a dense and a sparse model, which is itself the finding: the traps are not
# failing for want of capability, they fail because nothing licences answering "nothing
# is wrong". Both models patched the protected file rather than declining.
ARMS = [
    ("31B", "none (control)",          "gemma4_31b-it-bf16_audittrap_full_base"),
    ("31B", "system_local 341w",       "gemma4-coding_31b_audittrap_realism_syslocal"),
    ("31B", "skeptic_min 63w",         "gemma4-coding_31b_audittrap_skeptic_min"),
    ("31B", "skeptic_nochange 107w",   "gemma4-coding_31b_audittrap_skeptic_nochange"),
    ("26B", "none (control)",          "gemma4_26b-a4b-it-bf16_audittrap_verify_full"),
    ("26B", "system_local 341w",       "gemma4-coding_26b-a4b_audittrap_realism_syslocal"),
    ("26B", "skeptic_min 63w",         "gemma4-coding_26b-a4b_audittrap_skeptic_min"),
    ("26B", "skeptic_nochange 107w",   "gemma4-coding_26b-a4b_audittrap_skeptic_nochange"),
]

def load(tag):
    p = Path(f"results/audittrap/{tag}_latest.json")
    if not p.exists():
        return None
    rows = json.loads(p.read_text())
    return rows if isinstance(rows, list) else [rows]

print()
print("==== does a small generic prompt buy trap discipline, and what does it cost? ====")
print("traps are the target; claims and fixes are the bill. A prompt that lifts traps")
print("while dropping fixes has made the model timid, not careful.")
print()
head = f"{'model':6}{'prompt':22}{'claims':>10}{'fixes':>10}{'TRAPS':>10}{'total':>10}{'unclean':>9}"
print(head)
print("-" * len(head))
for model, label, tag in ARMS:
    rows = load(tag)
    if rows is None:
        print(f"{model:6}{label:22}{'not run':>10}")
        continue
    def bucket(keep):
        got = sum(int(r.get("score") or 0) for r in rows if r.get("task") in keep)
        mx = sum(int(r.get("max_score") or 0) for r in rows if r.get("task") in keep)
        return f"{got}/{mx}" if mx else "-"
    claims = bucket({"claim_battery"})
    fixes = bucket(FIXES)
    traps = bucket(TRAPS)
    tot = sum(int(r.get("score") or 0) for r in rows)
    totmax = sum(int(r.get("max_score") or 0) for r in rows)
    bad = sum(1 for r in rows if str(r.get("done_reason")) != "stop")
    print(f"{model:6}{label:22}{claims:>10}{fixes:>10}{traps:>10}{f'{tot}/{totmax}':>10}{bad:>9}")

print()
print("Per-trap detail: what the model actually answered, since 'unchanged' also has to")
print("carry a reason the grader accepts, and a lucky no-op with a wrong reason scores 0.")
for model, label, tag in ARMS:
    rows = load(tag)
    if not rows:
        continue
    for r in rows:
        if r.get("task") in TRAPS:
            ans = r.get("answer") or {}
            status = ans.get("status") if isinstance(ans, dict) else None
            print(f"  {model:5} {label:22} {r.get('task'):22} "
                  f"{int(r.get('score') or 0):>2}/{int(r.get('max_score') or 0):<3} "
                  f"status={str(status)[:12]:12} {str(r.get('grade_detail'))[:60]}")
print("==== ALL DONE gemma trap prompt small", flush=True)
PY
