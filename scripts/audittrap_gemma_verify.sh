#!/bin/zsh
# Gemma 4 26B-A4B scored 38/40 at BASE with thinking off, in 81 seconds. Fast and
# eager. Eagerness is exactly what the false-bug traps exist to punish: a model
# that patches whatever it is shown scores identically to one that reasons, until
# it is handed a non-bug. So run the best-scoring gemma config against the FULL
# suite (repairs + traps), and repeat it once for variance.
#
# BASE ONLY: the sysgen_* candidates are gone, and the system prompt is forced
# off. The matrix already settled this for gemma -- on the 26B MoE the system
# prompt cost 8 points (38 -> 30/40) and multiplied output 10.7x (641 -> 6876
# tokens/task), while the dense 31B was indifferent (38/40 either way, 389 vs 418
# tokens). Since we are after settings usable for real coding, a prompt that only
# ever hurts one model and never helps the other has no place in the candidate set.
#
# Config is chosen from the matrix results rather than hardcoded, and the whole
# thing is chained behind the qwopus verification so nothing shares the GPU.
#
# Caveat on candidates: gemma4_31b fix_med is the cell that ran while the laptop
# suspended, so its two timeouts are an artifact and its score will lose here on
# merit it never had a chance to show. Its clean re-run is chained AFTER this
# script, so this selection cannot see it. Acceptable: fixbase already scores
# 38/40 for both models, so the winner is almost certainly a think-off config.
set -euo pipefail
cd "$(dirname "$0")/.."
LOG=results/audittrap/gemma_verify.log
PREV_LOG=results/audittrap/qwopus_verify.log
DONE_MARK='ALL DONE qwopus verification'
mkdir -p results/audittrap
exec >>"$LOG" 2>&1

echo "==== GEMMA verification (BASE only) $(date) ===="
for i in $(seq 1 4320); do
  if grep -q "$DONE_MARK" "$PREV_LOG" 2>/dev/null; then
    echo "==== qwopus verification finished, proceeding $(date) ===="
    break
  fi
  sleep 10
done
if ! grep -q "$DONE_MARK" "$PREV_LOG" 2>/dev/null; then
  echo "==== qwopus verification never completed after 12h, aborting $(date) ====" >&2
  exit 2
fi

have_model() {
  ollama list 2>/dev/null | awk -v m="$1" '$1 == m { found = 1 } END { exit found ? 0 : 1 }'
}

# Pick the winning gemma BASE config from the matrix: model and think budget.
.venv/bin/python - <<'PY' > /tmp/gemma_best.txt
import json
from pathlib import Path

FIXES = [
    "runner_interrupt_scored",
    "chat_timeout_dropped",
    "subprocess_stderr_dropped",
    "warmup_no_deadline",
]
# tag suffix -> (ollama model, think budget). BASE runs only.
CANDIDATES = {
    "gemma4_26b-a4b-it-bf16_audittrap_fixbase": ("gemma4:26b-a4b-it-bf16", "0"),
    "gemma4_26b-a4b-it-bf16_audittrap_fix_med": ("gemma4:26b-a4b-it-bf16", "medium"),
    "gemma4_31b-it-bf16_audittrap_fixbase":     ("gemma4:31b-it-bf16", "0"),
    "gemma4_31b-it-bf16_audittrap_fix_med":     ("gemma4:31b-it-bf16", "medium"),
}

best = None
for tag, (model, think) in CANDIDATES.items():
    p = Path(f"results/audittrap/{tag}_latest.json")
    if not p.exists():
        continue
    by = {r["task"]: r for r in json.loads(p.read_text())}
    if not all(t in by for t in FIXES):
        continue
    pts = sum(int(by[t].get("score") or 0) for t in FIXES)
    wall = sum(float(by[t].get("wall_s") or 0) for t in FIXES)
    # highest score wins; on a tie prefer the faster config
    key = (-pts, wall)
    if best is None or key < best[0]:
        best = (key, tag, model, think, pts)

if best:
    _, tag, model, think, pts = best
    print(f"{model}\t{think}\t{tag}\t{pts}")
PY

if [[ ! -s /tmp/gemma_best.txt ]]; then
  echo "==== no completed gemma BASE run found, nothing to verify $(date) ====" >&2
  echo "==== ALL DONE gemma verification"
  exit 0
fi

IFS=$'\t' read -r GM THINK SRC_TAG SRC_PTS < /tmp/gemma_best.txt
echo "==== best gemma BASE config: $SRC_TAG ($SRC_PTS/40) -> model=$GM think=$THINK ===="

if ! have_model "$GM"; then
  echo "==== SKIP: $GM not installed $(date) ====" >&2
  exit 3
fi

export BENCH_PROVIDER=ollama
export BENCH_TEMPERATURE=0.1 BENCH_MAX_ROUNDS=40 BENCH_MAX_TOOL_CALLS=40
export BENCH_TASK_TIMEOUT_S=1200 BENCH_NUM_CTX=65536
export BENCH_KEEP_ALIVE=24h BENCH_NUM_PREDICT=24576
export BENCH_THINK="$THINK" BENCH_THINK_MAX_CHARS=0
if [[ "$THINK" == "0" ]]; then
  export BENCH_THINK_PROMOTE=0 BENCH_THINK_LOOP=0
else
  export BENCH_THINK_PROMOTE=1 BENCH_THINK_LOOP=1
fi
export BENCH_SYSTEM_PROMPT=0
unset BENCH_SYSTEM_PROMPT_FILE BENCH_FINALIZE_AFTER BENCH_THINK_ROUNDS DS4_BASE || true

SLUG="${GM//:/_}"
SLUG="${SLUG//./}"

run() {
  local tag="$1"
  echo "==== START $tag $(date) ===="
  BENCH_MODEL="$GM" BENCH_TAG="$tag" .venv/bin/python -u -m benches.audittrap || \
    echo "==== FAILED $tag $(date) ====" >&2
  echo "==== DONE $tag $(date) ===="
  ollama stop "$GM" 2>/dev/null || true
}

# Full suite: repairs AND the two planted false bugs.
unset BENCH_TASKS || true
run "${SLUG}_audittrap_verify_full"

# One repeat of the fix-only set, to size the run-to-run variance.
export BENCH_TASKS='runner_interrupt_scored,chat_timeout_dropped,subprocess_stderr_dropped,warmup_no_deadline'
run "${SLUG}_audittrap_verify_fix_rep2"

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

print("\n==== Gemma verification: does the fast, eager fixer respect the traps? ====")
for p in sorted(Path("results/audittrap").glob("gemma4*_audittrap_verify_*_latest.json")):
    by = {r["task"]: r for r in json.loads(p.read_text())}
    fg = sum(int(by[t].get("score") or 0) for t in FIXES if t in by)
    fm = sum(int(by[t].get("max_score") or 0) for t in FIXES if t in by)
    tg = sum(int(by[t].get("score") or 0) for t in TRAPS if t in by)
    tm = sum(int(by[t].get("max_score") or 0) for t in TRAPS if t in by)
    name = p.name.replace("_latest.json", "")
    trap = f"traps {tg}/{tm}" if tm else "traps not run"
    print(f"{name:52} fixes {fg:2}/{fm:2}  {trap}")
print("\nHigh fixes with low traps = patches anything it is shown. Both must hold.")
print("Note: gemma's claim run went 12/12 on planted-false claims but denied 3 of 8")
print("true ones, so expect strong trap scores and watch for under-claiming instead.")
print("==== ALL DONE gemma verification", flush=True)
PY
