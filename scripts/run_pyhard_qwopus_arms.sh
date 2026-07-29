#!/bin/zsh
# Why this exists: Qwopus scored 27/99 on pyhard, but only two of nine tasks
# emitted any code. Three causes were tangled together, and the goal here is
# settings usable for REAL coding, not settings that let a benchmark rescue the
# model. That distinction drives the arm design below.
#
# The harness has three rescues that no editor (Cursor, aider, Continue, plain
# llama-server) provides:
#   ThinkLoopDetector      -- aborts verbatim repetition        (BENCH_THINK_LOOP)
#   nudge-and-retry        -- re-prompts once after that abort  (pyhard bench.py)
#   think promotion        -- digs a final out of the think text (BENCH_THINK_PROMOTE)
# With all three on, Qwopus still looped on 5/9 tasks AND on all 5 retries. In an
# editor those are ten dead requests, so the realism arms below switch the rescues
# off and measure what a developer would actually experience.
#
# Arms, cheapest-and-most-decisive first:
#   1 nothink        think off, stock model. No think channel, so no loop is
#                    possible; this is the likely deployable config and the fair
#                    headline number.
#   2 antiloop_raw   think med, ANTI-LOOP model, all rescues OFF. The actual
#                    question: do sampler penalties stop the attractor forming?
#                    presence_penalty 1.5 (Qwen's documented remedy for endless
#                    repetition) + repeat_penalty 1.1 (Ollama's default, which
#                    stock Qwen sampling disables at 1). gemma4 ships no
#                    PARAMETER lines, inherits 1.1, and never looped once.
#   3 think_full     think med, stock model, rescues ON, budget left to the
#                    harness. Isolates a config error of mine: the other-bench
#                    queue exported BENCH_NUM_PREDICT=24576 for every bench and
#                    silently halved pyhard's think-on default of 49152.
#   4 think_raw      think med, stock model, all rescues OFF. The unvarnished
#                    real-editor baseline that gives arm 2 its meaning.
#
# CONFOUND, deliberately accepted: pyhard hardcodes temperature 0.1 in OPTIONS
# with no env override, so every arm runs near-greedy no matter what the Modelfile
# says. Near-greedy is itself a loop trigger, which makes arm 2 a CONSERVATIVE
# test -- if the penalties defeat the attractor at 0.1 they will also defeat it at
# the author-recommended 0.6. A 0.6 arm needs the pyhard temperature fix first.
#
# Realism arms cap tasks at 900s: a developer abandons a spinning request long
# before 30 minutes, and an undetected loop otherwise burns all 49152 tokens.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"

LOG="$ROOT/results/pyhard_qwopus_arms.log"
PREV_LOG="$ROOT/results/audittrap/gemma31b_fixmed_rerun.log"
DONE_MARK='ALL DONE gemma31b fixmed re-run'
STOCK='qwopus3.6:35b-a3b-coder-q8_0'
ANTILOOP='qwopus3.6:35b-a3b-coder-q8_0-antiloop'
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== pyhard qwopus arms $(date) ===="
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
export BENCH_OUT="$ROOT/results" BENCH_MERGE_LATEST=0
unset BENCH_TASKS BENCH_SYSTEM_PROMPT BENCH_SYSTEM_PROMPT_FILE DS4_BASE

# rescues: "on" keeps harness defaults, "off" strips every safety net
run_arm() {
  local model="$1" tag="$2" think="$3" rescues="$4" timeout="$5" note="$6"
  if ! have_model "$model"; then
    echo "==== SKIP $tag ($model not installed) ====" >&2
    return 0
  fi
  echo "---- START $tag model=$model think=$think rescues=$rescues timeout=${timeout}s $(date) ----"
  echo "     $note"
  (
    export BENCH_THINK="$think"
    export BENCH_TASK_TIMEOUT_S="$timeout"
    # never override num_predict: the harness raises it to 49152 when thinking,
    # and overriding it is exactly the mistake this run exists to correct
    unset BENCH_NUM_PREDICT
    if [[ "$rescues" == "off" ]]; then
      export BENCH_THINK_LOOP=0 BENCH_THINK_PROMOTE=0 BENCH_THINK_MAX_CHARS=0
    else
      unset BENCH_THINK_LOOP BENCH_THINK_PROMOTE BENCH_THINK_MAX_CHARS
    fi
    BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" -u "$ROOT/run.py" run pyhard
  ) || echo "WARN $tag rc=$?" >&2
  echo "---- DONE $tag $(date) ----"
  ollama stop "$model" 2>/dev/null || true
}

run_arm "$STOCK" 'qwopus3.6_35b-a3b-coder-q8_0_pyhard_nothink' '0' 'on' 1200 \
  'no think channel, so no loop possible: the likely deployable config'
run_arm "$ANTILOOP" 'qwopus3.6_35b-a3b-coder-q8_0_pyhard_antiloop_raw' 'medium' 'off' 900 \
  'presence_penalty 1.5 + repeat_penalty 1.1, no rescues: does the sampler fix hold?'
run_arm "$STOCK" 'qwopus3.6_35b-a3b-coder-q8_0_pyhard_think_full' 'medium' 'on' 1800 \
  'stock sampler, rescues on, harness 49152 budget: isolates the halved-budget error'
run_arm "$STOCK" 'qwopus3.6_35b-a3b-coder-q8_0_pyhard_think_raw' 'medium' 'off' 900 \
  'stock sampler, no rescues: the unvarnished real-editor baseline'

"$PY" - <<'PY'
import json
from pathlib import Path

ARMS = [
    ("orig: think med, npred 24576, rescues on", "qwopus3.6_35b-a3b-coder-q8_0_pyhard_pyhard"),
    ("1 nothink, rescues on", "qwopus3.6_35b-a3b-coder-q8_0_pyhard_nothink_pyhard"),
    ("2 ANTILOOP think med, rescues OFF", "qwopus3.6_35b-a3b-coder-q8_0_pyhard_antiloop_raw_pyhard"),
    ("3 think med, npred 49152, rescues on", "qwopus3.6_35b-a3b-coder-q8_0_pyhard_think_full_pyhard"),
    ("4 think med, rescues OFF", "qwopus3.6_35b-a3b-coder-q8_0_pyhard_think_raw_pyhard"),
    ("ref: qwen3.6 nothink", "qwen3.6_35b-a3b-coding-bf16_pyhard_nothink_pyhard"),
    ("ref: qwen3.6 think on", "qwen3.6_35b-a3b-coding-bf16_pyhard_pyhard"),
    ("ref: gemma4 26b nothink", "gemma4_26b-a4b-it-bf16_pyhard_pyhard"),
]
print("\n==== pyhard arms: does the model produce usable code without a safety net? ====")
hdr = f"{'arm':44}{'score':>8}{'npred':>8}{'blank':>7}{'loop':>6}{'trunc':>7}{'wall_s':>9}"
print(hdr)
print("-" * len(hdr))
for label, tag in ARMS:
    p = Path(f"results/{tag}_latest.json")
    if not p.exists():
        print(f"{label:44}{'--':>8}")
        continue
    rows = json.loads(p.read_text())
    pts = sum(int(r.get("score") or 0) for r in rows)
    mx = sum(int(r.get("max_score") or 0) for r in rows)
    npred = sorted({int(r.get("num_predict") or 0) for r in rows})
    blank = sum(1 for r in rows if not r.get("eval_tokens"))
    loop = sum(1 for r in rows if r.get("done_reason") == "think_loop")
    trunc = sum(1 for r in rows if r.get("done_reason") == "length")
    wall = sum(float(r.get("wall_s") or 0) for r in rows)
    print(
        f"{label:44}{f'{pts}/{mx}':>8}{','.join(map(str, npred)):>8}"
        f"{blank:>7}{loop:>6}{trunc:>7}{wall:>9.0f}"
    )
print()
print("blank = tasks that emitted zero output tokens (in an editor: a dead request)")
print("loop  = ThinkLoopDetector fired; only possible on rescues=on arms")
print("trunc = ran to num_predict. On rescues=off arms this is what an undetected")
print("        loop looks like: full budget burned, nothing usable returned.")
print()
print("Read it this way: arm 4 is the real-editor baseline for stock settings, and")
print("arm 2 says whether sampler penalties alone make think mode deployable. If")
print("arm 2 still shows blanks, Qwopus is a think-off-only model for real work.")
print("==== ALL DONE pyhard qwopus arms", flush=True)
PY
