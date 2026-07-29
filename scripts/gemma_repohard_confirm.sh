#!/bin/zsh
# The 26B scored 80/80 on repohard once, with a stop sequence and num_predict 24576,
# up from 43/80. Two parts of that need separating before it can be believed:
#
#   +20 points is mechanism, not luck. client_contract_drift and nplus1_reconciliation
#   went 0/10 -> 10/10 in ALL THREE stop arms, because both had been dying in a task
#   timeout while the model fabricated the harness's replies to itself. Deterministic.
#
#   The last 13 points are one explicable task and one suspicious one. race_webhook
#   went 3 -> 7 -> 10 as budget rose, ending on done_reason "length" at 8192 and
#   "stop" at 24576, which is a real mechanism. outbox_poison_retry went 0 -> 10 with
#   no mechanism at all: it had scored 0/10 in 458 tokens with no pathology. That one
#   looks like variance, and repohard variance is measured, not hypothetical -- a
#   qwen3.6 repeat pair swung 7 points per task at temperature 0.1.
#
# So: three repeats of the winning config. If 80/80 holds, the 26B ties the eight
# frontier models at the ceiling while running 13x faster than its original cell. If
# it lands nearer 67, the honest claim is "stop sequence fixes the timeouts" and the
# rest was noise. Also repeats the 31B, which has never been run twice on this bench.
#
# All arms: rescues off (BENCH_REALISM=1), sampler from the model's own Modelfile,
# and no finalize nudge -- that nudge cost 7 points and broke two passing tasks.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"

LOG="$ROOT/results/gemma_repohard_confirm.log"
PREV_LOG="$ROOT/results/gemma_trap_prompt_small.log"
DONE_MARK='ALL DONE gemma trap prompt small'
mkdir -p results
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== gemma repohard confirmation repeats $(date) ===="
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

run_rep() {
  local model="$1" tag="$2" note="$3"
  if ! have_model "$model"; then
    echo "---- SKIP $tag: $model missing ----" >&2
    return 0
  fi
  echo
  echo "---- START $tag model=$model $(date) ----"
  echo "     $note"
  (
    export BENCH_PROVIDER=ollama
    export BENCH_REALISM=1
    export BENCH_TEMPERATURE=auto
    export BENCH_THINK=0
    export BENCH_NUM_PREDICT=24576
    export BENCH_MAX_ROUNDS=40 BENCH_MAX_TOOL_CALLS=40
    export BENCH_TASK_TIMEOUT_S=1200 BENCH_NUM_CTX=65536
    export BENCH_KEEP_ALIVE=24h
    export BENCH_SYSTEM_PROMPT=0
    unset BENCH_SYSTEM_PROMPT_FILE BENCH_FINALIZE_AFTER || true
    BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" -u -m benches.repohard
  ) || echo "---- FAILED $tag rc=$? $(date) ----" >&2
  echo "---- DONE $tag $(date) ----"
  ollama stop "$model" 2>/dev/null || true
}

run_rep 'gemma4-coding:26b-a4b' 'gemma4-coding_26b-a4b_repohard_np24576_rep1' 'does 80/80 reproduce? repeat 1'
run_rep 'gemma4-coding:26b-a4b' 'gemma4-coding_26b-a4b_repohard_np24576_rep2' 'repeat 2'
run_rep 'gemma4-coding:31b'     'gemma4-coding_31b_repohard_np24576_rep1'     '31B at the same budget, never repeated on this bench'

"$PY" - <<'PY'
import json
from pathlib import Path

RUNS = [
    ("26B original (no stop, np 24576)", "gemma4_26b-a4b-it-bf16_repohard"),
    ("26B stop, np 8192 (A1)", "gemma4_26b-a4b-it-bf16_repohard_stop"),
    ("26B stop, np 24576 (A3)", "gemma4_26b-a4b-it-bf16_repohard_stop_np24576"),
    ("26B tuned, realism, np 8192", "gemma4-coding_26b-a4b_repohard_realism"),
    ("26B tuned, realism, np 24576 rep1", "gemma4-coding_26b-a4b_repohard_np24576_rep1"),
    ("26B tuned, realism, np 24576 rep2", "gemma4-coding_26b-a4b_repohard_np24576_rep2"),
    ("31B reference (temp 0.1, rescues on)", "gemma4_31b-it-bf16_repohard"),
    ("31B tuned, realism, np 8192", "gemma4-coding_31b_repohard_realism"),
    ("31B tuned, realism, np 24576 rep1", "gemma4-coding_31b_repohard_np24576_rep1"),
]

print()
print("==== repohard: does the fixed 26B hold up, and by how much? ====")
header = f"{'run':38}{'score':>8}{'tok/task':>10}{'wall/task':>11}{'unclean':>9}{'fake':>7}"
print(header)
print("-" * len(header))
scores_26, scores_31 = [], []
for label, tag in RUNS:
    p = Path(f"results/repohard/{tag}_latest.json")
    if not p.exists():
        print(f"{label:38}{'not run':>8}")
        continue
    rows = json.loads(p.read_text())
    n = len(rows) or 1
    sc = sum(int(r.get("score") or 0) for r in rows)
    tok = sum(int(r.get("eval_tokens") or 0) for r in rows)
    wall = sum(float(r.get("wall_s") or 0) for r in rows)
    bad = sum(1 for r in rows if str(r.get("done_reason")) != "stop")
    fake = 0
    for r in rows:
        t = r.get("transcript")
        if t and Path(t).exists():
            fake += Path(t).read_text(errors="ignore").count("<arch_result>")
    print(f"{label:38}{f'{sc}/80':>8}{tok / n:>10.0f}{f'{wall / n:.0f}s':>11}{bad:>9}{fake:>7}")
    if "np 24576 rep" in label and label.startswith("26B"):
        scores_26.append(sc)
    if "np 24576 rep" in label and label.startswith("31B"):
        scores_31.append(sc)

if scores_26:
    allv = [80] + scores_26  # A3 was the first observation of this config
    print()
    print(f"26B winning config across {len(allv)} runs: {allv}  mean {sum(allv) / len(allv):.1f}  range {max(allv) - min(allv)}")
    print("The +20 from the stop sequence is mechanism and should appear in every run.")
    print("A range above ~8 points means the rest of the claim is not yet supported.")
print("==== ALL DONE gemma repohard confirm", flush=True)
PY
