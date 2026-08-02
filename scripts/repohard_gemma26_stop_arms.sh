#!/bin/zsh
# gemma4 26B-A4B took 97 minutes on repohard for 43/80. The cause is not verbosity
# in the ordinary sense: on 4 of 8 tasks the model fabricates the harness half of
# the tool protocol, emitting <arch_tool> and then inventing the <arch_result>
# block that the harness is supposed to send back, over and over in one turn.
#
#   task                     fake <arch_result>   real tool calls   tokens   score
#   nplus1_reconciliation             103                 9          49,389   0/10 timeout
#   confused_deputy_admin              69                 6          25,440  10/10
#   migration_backfill_hole            59                 7          25,234  10/10
#   client_contract_drift              57                 8          25,259   0/10 timeout
#   money_rounding_split                3                 8           2,235  10/10
#   tenant_cache_key_collision          3                11           1,945  10/10
#   outbox_poison_retry                 0                 6             458   0/10
#   race_webhook_idempotency            0                12          24,943   3/10 length
#
# The dense 31B fabricates nothing on any of the 8 tasks and finishes in 8.9
# minutes for 77/80, so this is specific to the sparse model rather than the bench.
# parse_tool_call() takes the first match and discards the rest, so every fabricated
# block is already thrown away: pure waste, and on two tasks the waste is what
# caused the timeout.
#
# race_webhook is a second, different pathology: 11 terse rounds, then one round of
# 24,576 tokens of plain-text deliberation ("Wait, looking at...", "Wait, I should
# check...") that never reaches <arch_final>. A stop sequence cannot catch that
# because there is no marker to stop on; FINALIZE_AFTER is the lever for it.
#
# Three arms, one variable at a time. The already-queued R4 arm (num_predict back to
# the 8192 default, no stop) is the control for A1.
#   A1  stop=<arch_result>                     does killing fabrication alone recover the timeouts?
#   A2  stop + FINALIZE_AFTER=12               adds the lever for the deliberation runaway
#   A3  stop, num_predict pinned back to 24576 does a large budget still hurt once fabrication is gone?
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"

LOG="$ROOT/results/repohard_gemma26_stop_arms.log"
PREV_LOG="$ROOT/results/audittrap/gemma31b_full_suite.log"
DONE_MARK='ALL DONE gemma31b full suite'
GM='gemma4:26b-a4b-it-bf16'
mkdir -p results
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== gemma26 repohard stop-sequence arms $(date) ===="
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
if ! have_model "$GM"; then
  echo "==== SKIP: $GM not installed $(date) ====" >&2
  echo "==== ALL DONE gemma26 stop arms"
  exit 0
fi

run_arm() {
  local tag="$1" finalize="$2" npredict="$3" note="$4"
  echo
  echo "---- START $tag finalize=${finalize:-off} num_predict=${npredict:-default} $(date) ----"
  echo "     $note"
  (
    export BENCH_PROVIDER=ollama
    export BENCH_TEMPERATURE=0.1
    export BENCH_MAX_ROUNDS=40 BENCH_MAX_TOOL_CALLS=40
    export BENCH_TASK_TIMEOUT_S=1200 BENCH_NUM_CTX=65536
    export BENCH_KEEP_ALIVE=24h
    export BENCH_THINK=0
    export BENCH_SYSTEM_PROMPT=0
    export BENCH_STOP_FABRICATION=1
    unset BENCH_SYSTEM_PROMPT_FILE || true
    if [[ -n "$finalize" ]]; then export BENCH_FINALIZE_AFTER="$finalize"; else unset BENCH_FINALIZE_AFTER || true; fi
    if [[ -n "$npredict" ]]; then export BENCH_NUM_PREDICT="$npredict"; else unset BENCH_NUM_PREDICT || true; fi
    BENCH_MODEL="$GM" BENCH_TAG="$tag" "$PY" -u -m benches.repohard
  ) || echo "---- FAILED $tag rc=$? $(date) ----" >&2
  echo "---- DONE $tag $(date) ----"
  ollama stop "$GM" 2>/dev/null || true
}

run_arm 'gemma4_26b-a4b-it-bf16_repohard_stop'          ''   '' \
  'A1: stop=<arch_result> only. Control is the queued R4 arm (same budget, no stop).'
run_arm 'gemma4_26b-a4b-it-bf16_repohard_stop_fin12'    '12' '' \
  'A2: adds the finalize nudge for the plain-text deliberation runaway.'
run_arm 'gemma4_26b-a4b-it-bf16_repohard_stop_np24576'  ''   '24576' \
  'A3: stop plus the original oversized budget, to see if budget still matters.'

"$PY" - <<'PY'
import json
from pathlib import Path

ARMS = [
    ("original (np 24576, no stop)", "gemma4_26b-a4b-it-bf16_repohard"),
    ("R4 (np default, no stop)", "gemma4_26b-a4b-it-bf16_repohard_npdefault"),
    ("A1 stop", "gemma4_26b-a4b-it-bf16_repohard_stop"),
    ("A2 stop + finalize 12", "gemma4_26b-a4b-it-bf16_repohard_stop_fin12"),
    ("A3 stop + np 24576", "gemma4_26b-a4b-it-bf16_repohard_stop_np24576"),
    ("gemma4 31B reference", "gemma4_31b-it-bf16_repohard"),
]

print()
print("==== did killing the fabricated tool dialogue help? ====")
header = f"{'arm':30}{'score':>8}{'tok/task':>10}{'wall/task':>11}{'bad ends':>10}{'fake results':>14}"
print(header)
print("-" * len(header))

for label, tag in ARMS:
    p = Path(f"results/repohard/{tag}_latest.json")
    if not p.exists():
        print(f"{label:30}{'not run':>8}")
        continue
    rows = json.loads(p.read_text())
    n = len(rows) or 1
    sc = sum(int(r.get("score") or 0) for r in rows)
    mx = sum(int(r.get("max_score") or 0) for r in rows)
    tok = sum(int(r.get("eval_tokens") or 0) for r in rows)
    wall = sum(float(r.get("wall_s") or 0) for r in rows)
    bad = sum(1 for r in rows if str(r.get("done_reason")) != "stop")
    fake = 0
    for r in rows:
        t = r.get("transcript")
        if t and Path(t).exists():
            fake += Path(t).read_text(errors="ignore").count("<arch_result>")
    print(
        f"{label:30}{f'{sc}/{mx}':>8}{tok / n:>10.0f}{f'{wall / n:.0f}s':>11}{bad:>10}{fake:>14}"
    )

print()
print("Two separate pathologies, so read the columns separately: fake results should")
print("go to zero on every stop arm, and 'bad ends' only clears if the deliberation")
print("runaway is also handled. A recovered timeout is not the same as a recovered")
print("point -- outbox_poison_retry scored 0/10 in 458 tokens with no pathology at all.")
print("==== ALL DONE gemma26 stop arms", flush=True)
PY
