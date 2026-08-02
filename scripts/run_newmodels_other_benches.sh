#!/bin/zsh
# Qwopus and Gemma 4 have only faced audittrap. Put them through the other four
# benches so they sit on the same leaderboard as every other local model:
#   claim    20 true/false traps over shopapi (discrimination)
#   arch      9 tool-first exploration tasks on a planted-buggy shopapi
#   pyhard    9 from-scratch Python tasks, 99 pts (no tools)
#   repohard  8 explore-and-patch tasks on the synthetic ledgerkit repo
#
# Think budget per model is read from that model's own audittrap results rather
# than guessed: Qwopus scored 40/40 at think=medium and 0/40 at think=off, so a
# single hardcoded setting would have libelled one model or the other.
# Runs straight after the audittrap matrix; the round-cap probe and both trap
# verifications are chained behind THIS, so nothing shares the GPU.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"

LOG="$ROOT/results/newmodels_other_benches.log"
PREV_LOG="$ROOT/results/audittrap/local_newmodels_fixes_queue.log"
DONE_MARK='ALL DONE new-model fix-ticket queue'
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== new-model other-bench queue $(date) ===="

# wait up to 16h for the audittrap chain to drain
for i in $(seq 1 5760); do
  if grep -q "$DONE_MARK" "$PREV_LOG" 2>/dev/null; then
    echo "==== audittrap matrix finished, proceeding $(date) ===="
    break
  fi
  sleep 10
done
if ! grep -q "$DONE_MARK" "$PREV_LOG" 2>/dev/null; then
  echo "==== audittrap matrix never finished after 16h, aborting $(date) ====" >&2
  exit 2
fi

have_model() {
  ollama list 2>/dev/null | awk -v m="$1" '$1 == m { found = 1 } END { exit found ? 0 : 1 }'
}

# Pick each model's best think budget from its own audittrap BASE runs.
"$PY" - <<'PY' > /tmp/newmodels_think.txt
import json
from pathlib import Path

FIXES = [
    "runner_interrupt_scored",
    "chat_timeout_dropped",
    "subprocess_stderr_dropped",
    "warmup_no_deadline",
]
# model -> [(think budget, audittrap tag)], BASE (no system prompt) only
MODELS = {
    "qwopus3.6:35b-a3b-coder-q8_0": [
        ("0", "qwopus3.6_35b-a3b-coder-q8_0_audittrap_fix_off"),
        ("medium", "qwopus3.6_35b-a3b-coder-q8_0_audittrap_fix_med"),
        ("high", "qwopus3.6_35b-a3b-coder-q8_0_audittrap_fix_high"),
    ],
    "gemma4:26b-a4b-it-bf16": [
        ("0", "gemma4_26b-a4b-it-bf16_audittrap_fixbase"),
        ("medium", "gemma4_26b-a4b-it-bf16_audittrap_fix_med"),
    ],
    "gemma4:31b-it-bf16": [
        ("0", "gemma4_31b-it-bf16_audittrap_fixbase"),
        ("medium", "gemma4_31b-it-bf16_audittrap_fix_med"),
    ],
}

for model, cands in MODELS.items():
    best = None
    for think, tag in cands:
        p = Path(f"results/audittrap/{tag}_latest.json")
        if not p.exists():
            continue
        by = {r["task"]: r for r in json.loads(p.read_text())}
        if not all(t in by for t in FIXES):
            continue
        pts = sum(int(by[t].get("score") or 0) for t in FIXES)
        wall = sum(float(by[t].get("wall_s") or 0) for t in FIXES)
        key = (-pts, wall)  # best score, faster wins ties
        if best is None or key < best[0]:
            best = (key, think, pts, tag)
    if best is None:
        # no audittrap evidence: think-off is this repo's standing default
        print(f"{model}\t0\tno-evidence")
    else:
        _, think, pts, tag = best
        print(f"{model}\t{think}\t{tag}@{pts}/40")
PY

echo "---- chosen think budgets ----"
cat /tmp/newmodels_think.txt

export BENCH_PROVIDER=ollama
export BENCH_THINK_MAX_CHARS=0
export BENCH_NUM_CTX=65536
export BENCH_NUM_PREDICT=24576
export BENCH_TEMPERATURE=0.1
export BENCH_TASK_TIMEOUT_S=1200
export BENCH_OUT="$ROOT/results"
export BENCH_MERGE_LATEST=0
# audittrap-only knob; the other four benches carry their own prompts
unset BENCH_TASKS BENCH_SYSTEM_PROMPT BENCH_SYSTEM_PROMPT_FILE BENCH_THINK_ROUNDS DS4_BASE

run_bench() {
  local model="$1" bench="$2" tag="$3"
  echo "---- START $bench model=$model tag=$tag think=$BENCH_THINK $(date) ----"
  # repohard patches the fixture in place; a dirty tree silently poisons the next run
  git -C "$ROOT" checkout -- benches/repohard/fixture/ledgerkit/ 2>/dev/null || true
  BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" -u "$ROOT/run.py" run "$bench" || \
    echo "WARN $bench rc=$? model=$model" >&2
  echo "---- DONE $bench model=$model tag=$tag $(date) ----"
  "$PY" -u "$ROOT/run.py" report "$bench" --no-color || true
}

while IFS=$'\t' read -r model think src; do
  [[ -z "$model" ]] && continue
  if ! have_model "$model"; then
    echo "==== SKIP $model (not installed) $(date) ====" >&2
    continue
  fi
  safe="$(echo "$model" | sed 's/[^a-zA-Z0-9._-]/_/g')"
  echo "==== MODEL $model think=$think (from $src) $(date) ===="
  export BENCH_THINK="$think"
  if [[ "$think" == "0" ]]; then
    export BENCH_THINK_PROMOTE=0 BENCH_THINK_LOOP=0
  else
    export BENCH_THINK_PROMOTE=1 BENCH_THINK_LOOP=1
  fi

  # cheapest and most discriminative first, heaviest last
  run_bench "$model" claim    "${safe}_claim"
  run_bench "$model" arch     "${safe}_arch"
  run_bench "$model" pyhard   "${safe}_pyhard"
  run_bench "$model" repohard "${safe}_repohard"

  ollama stop "$model" 2>/dev/null || true
done < /tmp/newmodels_think.txt

echo "==== final leaderboards $(date) ===="
for b in claim arch pyhard repohard audittrap; do
  echo "---- $b ----"
  "$PY" -u "$ROOT/run.py" report "$b" --no-color || true
done
echo "==== ALL DONE new-model other-bench queue"
