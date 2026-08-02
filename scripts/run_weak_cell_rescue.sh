#!/bin/zsh
# Targeted retries of the weak cells, one variable each, aimed at the CAUSE that
# the transcripts actually show rather than at the score. Confidence is stated per
# arm so a null result is still informative.
#
#   R1 qwopus arch, ANTI-LOOP sampler            HIGH confidence
#      All three failures were done_reason=think_loop at rounds=32 -- the same
#      verbatim-repetition pathology as pyhard, not the round cap I first assumed.
#      presence_penalty 1.5 + repeat_penalty 1.1 are the brakes stock Qwen sampling
#      disables (repeat_penalty 1, presence_penalty 0). gemma inherits Ollama's 1.1
#      and never loops anywhere.
#
#   R2 qwopus arch, ANTI-LOOP sampler + temperature 0.6   MEDIUM
#      Adds the author-recommended temperature on top of R1. Near-greedy 0.1 is
#      itself a loop trigger: it is why the harness nudge-and-retry fell straight
#      back into the same attractor on all five pyhard loops. Second variable, so
#      it only gets interpreted if R1 alone was not enough.
#
#   R3 qwopus repohard, think=high                LOW -- expected to fail
#      Its 4 failures are NOT repetition (checked: zero duplicate lines) and NOT
#      malformed diffs (they apply, then pytest fails). They are simply wrong
#      fixes, which no sampler knob addresses. More reasoning budget is the only
#      configuration lever left, and audittrap says high scored WORSE than medium
#      there (35 vs 40/40), so this arm mostly exists to rule the lever out.
#
#   R4 gemma4 26B repohard, num_predict left at the harness default   HIGH
#      Gemma is terse here on the tasks that pass (149 and 57 tok/round) but on 5
#      of 8 tasks a single response runs away to ~24576 tokens, and
#      race_webhook_idempotency records done_reason=length. repohard's own default
#      is 8192; the other-bench queue exported BENCH_NUM_PREDICT=24576 for every
#      bench and TRIPLED it, giving each runaway three times the rope. Unsetting it
#      is the whole fix.
#
#   R5 gemma4 26B claim, think=medium             LOW-MEDIUM
#      Its 3 misses are all false negatives on ordering questions (does the ack
#      happen before the publish, does mark_paid run after the outbox insert) while
#      it rejected 12 of 12 planted false claims. Cheap to test whether reasoning
#      buys the confirmations, but thinking cost this model 11 points on audittrap,
#      so it may well backfire.
#
# Standing correction baked in: no arm overrides BENCH_NUM_PREDICT. That single
# uniform export damaged two separate cells in opposite directions -- halving
# pyhard's think budget (49152 -> 24576) and tripling repohard's (8192 -> 24576).
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"

LOG="$ROOT/results/weak_cell_rescue.log"
PREV_LOG="$ROOT/results/pyhard_qwopus_arms.log"
DONE_MARK='ALL DONE pyhard qwopus arms'
STOCK='qwopus3.6:35b-a3b-coder-q8_0'
ANTILOOP='qwopus3.6:35b-a3b-coder-q8_0-antiloop'
G26='gemma4:26b-a4b-it-bf16'
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== weak-cell rescue $(date) ===="
for i in $(seq 1 8640); do
  grep -q "$DONE_MARK" "$PREV_LOG" 2>/dev/null && { echo "==== chain drained, proceeding $(date) ===="; break; }
  sleep 10
done
if ! grep -q "$DONE_MARK" "$PREV_LOG" 2>/dev/null; then
  echo "==== chain never drained after 24h, aborting $(date) ====" >&2
  exit 2
fi

have_model() {
  ollama list 2>/dev/null | awk -v m="$1" '$1 == m { found = 1 } END { exit found ? 0 : 1 }'
}

export BENCH_PROVIDER=ollama
export BENCH_NUM_CTX=65536
export BENCH_MAX_ROUNDS=40 BENCH_MAX_TOOL_CALLS=40
export BENCH_TASK_TIMEOUT_S=1200
export BENCH_KEEP_ALIVE=24h
export BENCH_OUT="$ROOT/results" BENCH_MERGE_LATEST=0
export BENCH_SYSTEM_PROMPT=0
unset BENCH_SYSTEM_PROMPT_FILE BENCH_TASKS BENCH_FINALIZE_AFTER DS4_BASE || true

# arm: model, bench, tag, think, temperature ("" = bench default 0.1), note
run_arm() {
  local model="$1" bench="$2" tag="$3" think="$4" temp="$5" note="$6"
  if ! have_model "$model"; then
    echo "==== SKIP $tag ($model not installed) ====" >&2
    return 0
  fi
  echo "---- START $tag bench=$bench model=$model think=$think temp=${temp:-0.1-default} $(date) ----"
  echo "     $note"
  (
    export BENCH_THINK="$think"
    # never pin num_predict: each bench raises its own default when thinking
    unset BENCH_NUM_PREDICT
    if [[ -n "$temp" ]]; then export BENCH_TEMPERATURE="$temp"; else unset BENCH_TEMPERATURE; fi
    if [[ "$think" == "0" ]]; then
      export BENCH_THINK_PROMOTE=0 BENCH_THINK_LOOP=0 BENCH_THINK_MAX_CHARS=0
    else
      export BENCH_THINK_PROMOTE=1 BENCH_THINK_LOOP=1 BENCH_THINK_MAX_CHARS=0
    fi
    BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" -u "$ROOT/run.py" run "$bench"
  ) || echo "WARN $tag rc=$?" >&2
  echo "---- DONE $tag $(date) ----"
  ollama stop "$model" 2>/dev/null || true
}

run_arm "$ANTILOOP" arch 'qwopus3.6_35b-a3b-coder-q8_0_arch_antiloop' 'medium' '' \
  'R1 HIGH: kill the 3 think_loops with presence_penalty 1.5 + repeat_penalty 1.1'
run_arm "$ANTILOOP" arch 'qwopus3.6_35b-a3b-coder-q8_0_arch_antiloop_t06' 'medium' '0.6' \
  'R2 MEDIUM: R1 plus the author-recommended temperature'
run_arm "$G26" repohard 'gemma4_26b-a4b-it-bf16_repohard_npdefault' '0' '' \
  'R4 HIGH: num_predict back to the repohard default 8192, capping runaway rounds'
run_arm "$G26" claim 'gemma4_26b-a4b-it-bf16_claim_think_med' 'medium' '' \
  'R5 LOW-MED: does reasoning buy the 3 ordering confirmations it denied?'
run_arm "$STOCK" repohard 'qwopus3.6_35b-a3b-coder-q8_0_repohard_think_high' 'high' '' \
  'R3 LOW: wrong fixes, not formatting; ruling out the reasoning-budget lever'

"$PY" - <<'PY'
import json
from pathlib import Path


def load(path):
    p = Path(path)
    if not p.exists():
        return None
    rows = json.loads(p.read_text())
    return rows if isinstance(rows, list) else [rows]


def summarize(rows):
    if not rows:
        return None
    return {
        "pts": sum(int(r.get("score") or 0) for r in rows),
        "mx": sum(int(r.get("max_score") or 0) for r in rows),
        "wall": sum(float(r.get("wall_s") or 0) for r in rows),
        "tok": sum(int(r.get("eval_tokens") or 0) for r in rows),
        "loops": sum(1 for r in rows if r.get("done_reason") == "think_loop"),
        "stuck": sum(
            1 for r in rows if r.get("done_reason") in ("length", "task_timeout")
        ),
        "npred": sorted({int(r.get("num_predict") or 0) for r in rows}),
    }


ARCH = "results/archbench/qwopus3.6_35b-a3b-coder-q8_0_arch"
REPO = "results/repohard/qwopus3.6_35b-a3b-coder-q8_0_repohard"
G26C = "results/archbench/gemma4_26b-a4b-it-bf16_claim"
G26R = "results/repohard/gemma4_26b-a4b-it-bf16_repohard"

ARMS = [
    ("R1 qwopus arch antiloop", f"{ARCH}_latest.json", f"{ARCH}_antiloop_latest.json"),
    ("R2 qwopus arch antiloop t0.6", f"{ARCH}_latest.json", f"{ARCH}_antiloop_t06_latest.json"),
    ("R4 gemma26 repohard npred def", f"{G26R}_latest.json", f"{G26R}_npdefault_latest.json"),
    ("R5 gemma26 claim think=med", f"{G26C}_latest.json", f"{G26C}_think_med_latest.json"),
    ("R3 qwopus repohard think=high", f"{REPO}_latest.json", f"{REPO}_think_high_latest.json"),
]

print()
print("==== weak-cell rescue: did the configuration change move the CAUSE? ====")
header = (
    f"{'arm':32}{'before':>10}{'after':>10}{'delta':>7}"
    f"{'loops b>a':>11}{'stuck b>a':>11}{'wall b>a':>18}{'npred after':>13}"
)
print(header)
print("-" * len(header))

for label, before_path, after_path in ARMS:
    before = summarize(load(before_path))
    after = summarize(load(after_path))
    if before is None or after is None:
        print(f"{label:32}{'--':>10}{'not run':>10}")
        continue
    score_b = f"{before['pts']}/{before['mx']}"
    score_a = f"{after['pts']}/{after['mx']}"
    loops = f"{before['loops']} > {after['loops']}"
    stuck = f"{before['stuck']} > {after['stuck']}"
    wall = f"{before['wall']:.0f}s > {after['wall']:.0f}s"
    npred = ",".join(str(n) for n in after["npred"])
    delta = after["pts"] - before["pts"]
    print(
        f"{label:32}{score_b:>10}{score_a:>10}{delta:>+7}"
        f"{loops:>11}{stuck:>11}{wall:>18}{npred:>13}"
    )

print()
print("Judge each arm on its cause column, not on the score:")
print("  R1/R2 succeed if loops reach 0, whatever the score does.")
print("  R4 succeeds if stuck reaches 0 and wall falls; npred should read 8192.")
print("  R3 is expected flat: its failures were wrong fixes, not a budget limit.")
print("  Ignore repohard score deltas under 8 points -- a measured repeat pair")
print("  swung 7 points per task at this temperature.")
print("==== ALL DONE weak-cell rescue", flush=True)
PY
