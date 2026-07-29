#!/bin/zsh
# Confirm the runtime switch before it becomes the recommendation.
#
# gemma4:31b-mlx-bf16 is the same weights as the deployed model on Ollama's MLX engine.
# It reproduced repohard task-for-task -- all eight identical, 74/80 -- in 129s against
# 519s, and held 20/20 traps and 16/21 claims. One thing diverged: it scored 0/10 on
# audittrap's subprocess_stderr_dropped where llama.cpp scores 10/10.
#
# That divergence is the reason for this script. Identical weights producing a different
# answer means the backend is not bit-exact, which is unsurprising in itself -- different
# kernels accumulate differently -- but it matters whether the effect is one unlucky task
# or a systematic tilt. Two signals to separate them:
#
#   REPEATS of audittrap and repohard. MTP fails that same task in 3 of 3 runs, so the
#   bench is deterministic enough that a repeat is informative. If MLX fails it every
#   time, it is the backend; if it varies, it is a borderline task.
#
#   THE THREE BENCHES it has not faced. llama.cpp bf16 baselines exist for claim
#   (22/23), arch (83/90) and pyhard (95/99). Those are the broad check on whether the
#   backend costs anything in general, as opposed to on one audittrap task.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"

LOG="$ROOT/results/mlxbf16_confirm.log"
mkdir -p results
: >>"$LOG"
exec >>"$LOG" 2>&1
echo "==== mlx-bf16 confirmation $(date) ===="

M='gemma4-coding:31b-mlxbf16'
have_model() { ollama list 2>/dev/null | awk -v m="$1" '$1 == m { f=1 } END { exit f?0:1 }'; }
have_model "$M" || { echo "==== $M missing ====" >&2; exit 2; }

audit_arm() {
  echo "---- START $1 $(date) ----"
  (
    export BENCH_PROVIDER=ollama BENCH_REALISM=1 BENCH_TEMPERATURE=auto BENCH_THINK=0
    export BENCH_MAX_ROUNDS=40 BENCH_MAX_TOOL_CALLS=40
    export BENCH_TASK_TIMEOUT_S=1200 BENCH_NUM_CTX=65536 BENCH_KEEP_ALIVE=24h
    unset BENCH_NUM_PREDICT BENCH_FINALIZE_AFTER BENCH_THINK_MAX_CHARS || true
    unset BENCH_THINK_LOOP BENCH_THINK_PROMOTE BENCH_STOP_FABRICATION || true
    export BENCH_SYSTEM_PROMPT=1 BENCH_SYSTEM_PROMPT_FILE='prompts/skeptic_min.md'
    BENCH_MODEL="$M" BENCH_TAG="$1" "$PY" -u -m benches.audittrap
  ) || echo "---- FAILED $1 ----" >&2
  echo "---- DONE $1 $(date) ----"
}

repo_arm() {
  echo "---- START $1 $(date) ----"
  git -C "$ROOT" checkout -- benches/repohard/fixture/ledgerkit/ 2>/dev/null || true
  (
    export BENCH_PROVIDER=ollama BENCH_REALISM=1 BENCH_TEMPERATURE=auto BENCH_THINK=0
    export BENCH_NUM_PREDICT=24576
    export BENCH_MAX_ROUNDS=40 BENCH_MAX_TOOL_CALLS=40
    export BENCH_TASK_TIMEOUT_S=1200 BENCH_NUM_CTX=65536 BENCH_KEEP_ALIVE=24h
    export BENCH_SYSTEM_PROMPT=0
    unset BENCH_SYSTEM_PROMPT_FILE BENCH_FINALIZE_AFTER || true
    BENCH_MODEL="$M" BENCH_TAG="$1" "$PY" -u -m benches.repohard
  ) || echo "---- FAILED $1 ----" >&2
  echo "---- DONE $1 $(date) ----"
}

audit_arm 'gemma4-coding_31b-mlxbf16_audittrap_skeptic_min_rep2'
repo_arm  'gemma4-coding_31b-mlxbf16_repohard_np24576_rep2'
audit_arm 'gemma4-coding_31b-mlxbf16_audittrap_skeptic_min_rep3'
repo_arm  'gemma4-coding_31b-mlxbf16_repohard_np24576_rep3'

echo
echo "======== the three benches MLX has not faced ========"
(
  export BENCH_PROVIDER=ollama
  export BENCH_THINK=0 BENCH_THINK_PROMOTE=0 BENCH_THINK_LOOP=0 BENCH_THINK_MAX_CHARS=0
  export BENCH_NUM_CTX=65536 BENCH_NUM_PREDICT=24576 BENCH_TEMPERATURE=0.1
  export BENCH_TASK_TIMEOUT_S=1200 BENCH_OUT="$ROOT/results" BENCH_MERGE_LATEST=0
  export BENCH_KEEP_ALIVE=24h
  unset BENCH_TASKS BENCH_SYSTEM_PROMPT BENCH_SYSTEM_PROMPT_FILE BENCH_THINK_ROUNDS DS4_BASE || true
  unset BENCH_REALISM || true
  for b in claim arch pyhard; do
    echo "---- START $b $(date) ----"
    BENCH_MODEL="$M" BENCH_TAG="gemma4-coding_31b-mlxbf16_${b}" "$PY" -u "$ROOT/run.py" run "$b" \
      || echo "---- FAILED $b ----" >&2
    echo "---- DONE $b $(date) ----"
  done
)
ollama stop "$M" 2>/dev/null || true

"$PY" - <<'PY'
import json
from pathlib import Path

FIXES = {"runner_interrupt_scored", "chat_timeout_dropped",
         "subprocess_stderr_dropped", "warmup_no_deadline"}
TRAPS = {"sat_assign_cleared", "sql_where_inside_join"}

print()
print("==== is the MLX backend a free 3.5x, or does it cost something? ====")
print("llama.cpp bf16 on the same weights: 16/21 claims, 38/40 fixes, 20/20 traps, 74/80")
print("repohard. Any consistent shortfall here is the price of the backend.")
print()
h = f"{'run':8}{'claims':>9}{'fixes':>9}{'traps':>9}{'total':>9}   fix tasks lost"
print(h); print("-" * (len(h) + 12))
for label, tag in [("run 1", "gemma4-coding_31b-mlxbf16_audittrap_skeptic_min"),
                   ("run 2", "gemma4-coding_31b-mlxbf16_audittrap_skeptic_min_rep2"),
                   ("run 3", "gemma4-coding_31b-mlxbf16_audittrap_skeptic_min_rep3")]:
    p = Path(f"results/audittrap/{tag}_latest.json")
    if not p.exists():
        print(f"{label:8}{'not run':>9}"); continue
    rows = json.loads(p.read_text())
    def b(k):
        g = sum(int(r.get("score") or 0) for r in rows if r.get("task") in k)
        m = sum(int(r.get("max_score") or 0) for r in rows if r.get("task") in k)
        return f"{g}/{m}"
    lost = [r["task"] for r in rows if r.get("task") in FIXES
            and int(r.get("score") or 0) < int(r.get("max_score") or 0)]
    print(f"{label:8}{b({'claim_battery'}):>9}{b(FIXES):>9}{b(TRAPS):>9}"
          f"{sum(int(r.get('score') or 0) for r in rows):>6}/81   {', '.join(lost) or 'none'}")

print()
print("repohard (llama.cpp bf16 reference: 74/80 in 519s):")
for label, tag in [("run 1", "gemma4-coding_31b-mlxbf16_repohard_np24576"),
                   ("run 2", "gemma4-coding_31b-mlxbf16_repohard_np24576_rep2"),
                   ("run 3", "gemma4-coding_31b-mlxbf16_repohard_np24576_rep3")]:
    p = Path(f"results/repohard/{tag}_latest.json")
    if not p.exists():
        print(f"  {label}: not run"); continue
    rows = json.loads(p.read_text())
    bad = sum(1 for r in rows if str(r.get("done_reason")) != "stop")
    print(f"  {label}: {sum(int(r.get('score') or 0) for r in rows)}/80  "
          f"{sum(float(r.get('wall_s') or 0) for r in rows):.0f}s  unclean={bad}")
print("==== ALL DONE mlxbf16 confirm", flush=True)
PY
