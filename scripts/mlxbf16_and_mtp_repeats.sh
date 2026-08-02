#!/bin/zsh
# Two loose ends, in order of how much they could change the recommendation.
#
# ONE: the MLX runtime, tested properly this time. The first MLX arm used
# gemma4:31b-mlx, which turned out to be nvfp4 -- 4 bits -- so it moved precision and
# backend together and cannot attribute its result to either. gemma4:31b-mlx-bf16 is
# the same weights as the deployed model on a different runner.
#
# This is worth the 62GB because of an anomaly the first arm threw up. Decode on a dense
# model cannot exceed the memory bus if it reads every weight once per token, and the
# llama.cpp bf16 model sits at 88% of that bound, which is why quantization was the only
# lever worth pulling. The nvfp4 MLX model measured 178% of the same bound -- 59 tok/s
# against 18.5GB resident -- and the wall clock agrees, 256 tokens in 4.4 seconds. So
# either size_vram means something different under the MLX runner, or that runner is not
# doing one pass per token. Only the bf16 arm can tell those apart, and if the second is
# true then the ceiling argument underpinning tonight's whole analysis is incomplete.
#
# TWO: reproducibility for the winner. MTP is the recommendation on the strength of one
# run per bench, and it lost exactly three isolated tasks -- subprocess_stderr_dropped
# on audittrap, outbox_poison_retry on repohard, one pyhard task. Isolated single-task
# losses are precisely the shape that variance takes. The bf16 31B scored identically
# across four separate runs earlier in this work, so this bench is stable enough that
# repeats mean something; MTP has never been asked to repeat anything.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"

LOG="$ROOT/results/mlxbf16_and_mtp_repeats.log"
PREV_LOG="$ROOT/results/mtp_full_and_variants.log"
DONE_MARK='ALL DONE mtp full and variants'
mkdir -p results
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== mlx-bf16 and MTP repeats $(date) ===="
for i in $(seq 1 8640); do
  grep -q "$DONE_MARK" "$PREV_LOG" 2>/dev/null && { echo "==== previous chain drained $(date) ===="; break; }
  sleep 10
done
grep -q "$DONE_MARK" "$PREV_LOG" 2>/dev/null || { echo "==== never drained ====" >&2; exit 2; }

have_model() { ollama list 2>/dev/null | awk -v m="$1" '$1 == m { f=1 } END { exit f?0:1 }'; }

pull_with_retry() {
  local model="$1"
  have_model "$model" && { echo "---- SKIP pull $model ----"; return 0; }
  local prog="$ROOT/results/pull_progress_${model//[:\/]/_}.txt"
  for attempt in 1 2 3; do
    echo "---- PULL $model attempt $attempt $(date) ----"
    ollama pull "$model" >"$prog" 2>&1
    tr '\r' '\n' <"$prog" | rg -v '^\s*$' | tail -2
    have_model "$model" && { echo "---- PULLED $model $(date) ----"; return 0; }
    sleep 60
  done
  echo "---- FAILED pull $model ----" >&2
  return 1
}

audit_arm() {
  local model="$1" tag="$2"
  have_model "$model" || { echo "---- SKIP $tag ----" >&2; return 0; }
  echo "---- START $tag $(date) ----"
  (
    export BENCH_PROVIDER=ollama BENCH_REALISM=1 BENCH_TEMPERATURE=auto BENCH_THINK=0
    export BENCH_MAX_ROUNDS=40 BENCH_MAX_TOOL_CALLS=40
    export BENCH_TASK_TIMEOUT_S=1200 BENCH_NUM_CTX=65536 BENCH_KEEP_ALIVE=24h
    unset BENCH_NUM_PREDICT BENCH_FINALIZE_AFTER BENCH_THINK_MAX_CHARS || true
    unset BENCH_THINK_LOOP BENCH_THINK_PROMOTE BENCH_STOP_FABRICATION || true
    export BENCH_SYSTEM_PROMPT=1 BENCH_SYSTEM_PROMPT_FILE='prompts/skeptic_min.md'
    BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" -u -m benches.audittrap
  ) || echo "---- FAILED $tag ----" >&2
  echo "---- DONE $tag $(date) ----"
  ollama stop "$model" 2>/dev/null || true
}

repo_arm() {
  local model="$1" tag="$2"
  have_model "$model" || { echo "---- SKIP $tag ----" >&2; return 0; }
  echo "---- START $tag $(date) ----"
  git -C "$ROOT" checkout -- benches/repohard/fixture/ledgerkit/ 2>/dev/null || true
  (
    export BENCH_PROVIDER=ollama BENCH_REALISM=1 BENCH_TEMPERATURE=auto BENCH_THINK=0
    export BENCH_NUM_PREDICT=24576
    export BENCH_MAX_ROUNDS=40 BENCH_MAX_TOOL_CALLS=40
    export BENCH_TASK_TIMEOUT_S=1200 BENCH_NUM_CTX=65536 BENCH_KEEP_ALIVE=24h
    export BENCH_SYSTEM_PROMPT=0
    unset BENCH_SYSTEM_PROMPT_FILE BENCH_FINALIZE_AFTER || true
    BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" -u -m benches.repohard
  ) || echo "---- FAILED $tag ----" >&2
  echo "---- DONE $tag $(date) ----"
  ollama stop "$model" 2>/dev/null || true
}

echo
echo "======== the MLX runtime at full precision ========"
if pull_with_retry 'gemma4:31b-mlx-bf16' \
   && ollama create 'gemma4-coding:31b-mlxbf16' -f "$ROOT/modelfiles/gemma4-31b-coding-mlxbf16.Modelfile" >/dev/null 2>&1; then
  echo "---- BUILT gemma4-coding:31b-mlxbf16 ----"
  "$PY" -u scripts/decode_speed_probe.py 'gemma4-coding:31b-mlxbf16' || true
  audit_arm 'gemma4-coding:31b-mlxbf16' 'gemma4-coding_31b-mlxbf16_audittrap_skeptic_min'
  repo_arm  'gemma4-coding:31b-mlxbf16' 'gemma4-coding_31b-mlxbf16_repohard_np24576'
else
  echo "---- mlx-bf16 unavailable ----" >&2
fi

echo
echo "======== does MTP reproduce? ========"
audit_arm 'gemma4-coding:31b-mtp' 'gemma4-coding_31b-mtp_audittrap_skeptic_min_rep2'
repo_arm  'gemma4-coding:31b-mtp' 'gemma4-coding_31b-mtp_repohard_np24576_rep2'
audit_arm 'gemma4-coding:31b-mtp' 'gemma4-coding_31b-mtp_audittrap_skeptic_min_rep3'
repo_arm  'gemma4-coding:31b-mtp' 'gemma4-coding_31b-mtp_repohard_np24576_rep3'

"$PY" - <<'PY'
import json
from pathlib import Path

TRAPS = {"sat_assign_cleared", "sql_where_inside_join"}
FIXES = {"runner_interrupt_scored", "chat_timeout_dropped",
         "subprocess_stderr_dropped", "warmup_no_deadline"}

print()
print("==== MTP across repeats: is the recommendation stable? ====")
print("Three isolated single-task losses are the shape variance takes. If the SAME task")
print("fails every time it is the model; if a different one fails each time it is noise")
print("and the single-run totals mean less than they appear to.")
print()
h = f"{'run':8}{'claims':>9}{'fixes':>9}{'traps':>9}{'total':>9}   which fix task failed"
print(h)
print("-" * (len(h) + 10))
for label, tag in [("run 1", "gemma4-coding_31b-mtp_audittrap_skeptic_min"),
                   ("run 2", "gemma4-coding_31b-mtp_audittrap_skeptic_min_rep2"),
                   ("run 3", "gemma4-coding_31b-mtp_audittrap_skeptic_min_rep3")]:
    p = Path(f"results/audittrap/{tag}_latest.json")
    if not p.exists():
        print(f"{label:8}{'not run':>9}")
        continue
    rows = json.loads(p.read_text())
    def b(keep):
        g = sum(int(r.get("score") or 0) for r in rows if r.get("task") in keep)
        m = sum(int(r.get("max_score") or 0) for r in rows if r.get("task") in keep)
        return f"{g}/{m}"
    lost = [r["task"] for r in rows
            if r.get("task") in FIXES and int(r.get("score") or 0) < int(r.get("max_score") or 0)]
    tot = sum(int(r.get("score") or 0) for r in rows)
    print(f"{label:8}{b({'claim_battery'}):>9}{b(FIXES):>9}{b(TRAPS):>9}{f'{tot}/81':>9}   "
          f"{', '.join(lost) or 'none'}")

print()
print("repohard:")
for label, tag in [("run 1", "gemma4-coding_31b-mtp_repohard_np24576"),
                   ("run 2", "gemma4-coding_31b-mtp_repohard_np24576_rep2"),
                   ("run 3", "gemma4-coding_31b-mtp_repohard_np24576_rep3")]:
    p = Path(f"results/repohard/{tag}_latest.json")
    if not p.exists():
        print(f"  {label}: not run")
        continue
    rows = json.loads(p.read_text())
    lost = [f"{r['task']}({r.get('score')})" for r in rows
            if int(r.get("score") or 0) < int(r.get("max_score") or 0)]
    bad = sum(1 for r in rows if str(r.get("done_reason")) != "stop")
    print(f"  {label}: {sum(int(r.get('score') or 0) for r in rows)}/80  unclean={bad}  "
          f"dropped: {', '.join(lost) or 'nothing'}")
print("==== ALL DONE mlxbf16 and mtp repeats", flush=True)
PY
