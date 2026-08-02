#!/bin/zsh
# Finish the job on the MTP model, then work along the rest of the gemma4 shelf.
#
# Established so far, all measured on this machine tonight:
#   31B bf16   8.5 tok/s   74/81 audittrap   74/80 repohard   no loops, ever
#   31B QAT   24.6 tok/s   49/81 audittrap   two 20-minute tool-call loops
#   31B MTP   24.7 tok/s   64/81 audittrap   full trap discipline, no loops
#
# MTP is the only variant that bought speed without breaking anything, because it
# keeps bf16 weights and gets throughput from predicting several tokens per forward
# pass. The bandwidth arithmetic confirms the heads are live: 24.7 tok/s x 62.6 GB is
# 252% of a memory bus that physically cannot be exceeded if you read every weight
# once per token, so it is plainly not doing that.
#
# STAGE B puts MTP through the three benches it has not faced, against baselines that
# already exist for gemma4:31b-it-bf16 (claim 22/23, arch 83/90, pyhard 95/99). Those
# were measured with an explicit temperature of 0.1 and rescues off at think=0, so the
# same conditions are used here rather than the Modelfile sampler -- otherwise the
# comparison would move two variables at once.
#
# STAGE D then screens the remaining shelf. Each candidate faces the cheap disqualifying
# gate first (throughput, trap suite, repohard) because that is what killed QAT in forty
# minutes; only a survivor earns the full five-bench treatment.
#
#   31b-it-q8_0   the informative midpoint. 4-bit destroyed judgement and introduced
#                 loops; bf16 has neither problem. 8-bit says whether the damage is a
#                 cliff below q8 or a slope, and ~33GB predicts ~16 tok/s.
#   31b-mlx       a different runtime rather than different weights. Ollama ships an
#                 MLX backend and Apple claims the M5 neural accelerators give 4x the
#                 prompt processing of M4, which would show up in prefill if anywhere.
#
# mxfp8 and nvfp4 are deliberately skipped: nvfp4 targets Blackwell tensor cores and
# neither has any reason to have a tuned Metal kernel. They can be added if q8 or mlx
# turns up something interesting.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"

LOG="$ROOT/results/mtp_full_and_variants.log"
PREV_LOG="$ROOT/results/speed_variant_gate.log"
DONE_MARK='ALL DONE speed variant gate'
mkdir -p results
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== MTP full bench and variant sweep $(date) ===="
if [[ "${BENCH_SKIP_GATE:-0}" == "1" ]]; then
  echo "==== gate skipped by request ===="
else
  for i in $(seq 1 8640); do
    grep -q "$DONE_MARK" "$PREV_LOG" 2>/dev/null && { echo "==== speed gate drained $(date) ===="; break; }
    sleep 10
  done
  grep -q "$DONE_MARK" "$PREV_LOG" 2>/dev/null || { echo "==== gate never drained $(date) ====" >&2; exit 2; }
fi

have_model() {
  ollama list 2>/dev/null | awk -v m="$1" '$1 == m { found = 1 } END { exit found ? 0 : 1 }'
}

pull_with_retry() {
  local model="$1"
  have_model "$model" && { echo "---- SKIP pull $model: present ----"; return 0; }
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

build_tuned() {
  local base="$1" name="$2" file="$3"
  have_model "$base" || return 1
  ollama create "$name" -f "$file" >/dev/null 2>&1 \
    && echo "---- BUILT $name ----" || { echo "---- FAILED build $name ----" >&2; return 1; }
}

speed() {
  echo "---- START speed probe $* $(date) ----"
  "$PY" -u scripts/decode_speed_probe.py "$@" || echo "---- FAILED speed probe ----" >&2
  echo "---- DONE speed probe $(date) ----"
}

# The audittrap and repohard arms reproduce the conditions the MTP and QAT arms ran
# under in the previous chain: tuned Modelfile sampler, every harness rescue disabled.
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

# claim/arch/pyhard go through run.py under the conditions the existing gemma baselines
# were measured with, not the Modelfile sampler, so the numbers are directly comparable.
other_benches() {
  local model="$1" safe="$2"
  have_model "$model" || { echo "---- SKIP other benches for $model ----" >&2; return 0; }
  (
    export BENCH_PROVIDER=ollama
    export BENCH_THINK=0 BENCH_THINK_PROMOTE=0 BENCH_THINK_LOOP=0 BENCH_THINK_MAX_CHARS=0
    export BENCH_NUM_CTX=65536 BENCH_NUM_PREDICT=24576 BENCH_TEMPERATURE=0.1
    export BENCH_TASK_TIMEOUT_S=1200 BENCH_OUT="$ROOT/results" BENCH_MERGE_LATEST=0
    export BENCH_KEEP_ALIVE=24h
    unset BENCH_TASKS BENCH_SYSTEM_PROMPT BENCH_SYSTEM_PROMPT_FILE BENCH_THINK_ROUNDS DS4_BASE || true
    unset BENCH_REALISM || true
    for b in claim arch pyhard; do
      echo "---- START $b model=$model $(date) ----"
      git -C "$ROOT" checkout -- benches/repohard/fixture/ledgerkit/ 2>/dev/null || true
      BENCH_MODEL="$model" BENCH_TAG="${safe}_${b}" "$PY" -u "$ROOT/run.py" run "$b" \
        || echo "---- FAILED $b rc=$? ----" >&2
      echo "---- DONE $b model=$model $(date) ----"
    done
  )
  ollama stop "$model" 2>/dev/null || true
}

echo
echo "======== STAGE B: MTP on the three benches it has not faced ========"
other_benches 'gemma4-coding:31b-mtp' 'gemma4-coding_31b-mtp'

echo
echo "======== STAGE C: MTP against every gemma baseline ========"
for b in claim arch pyhard repohard audittrap; do
  echo "---- $b ----"
  "$PY" -u "$ROOT/run.py" report "$b" --no-color 2>/dev/null | head -12 || true
done

echo
echo "======== STAGE D: screening the rest of the shelf ========"
screen_variant() {
  local base="$1" name="$2" file="$3" safe="$4"
  echo
  echo "-------- candidate $base --------"
  pull_with_retry "$base" || return 0
  build_tuned "$base" "$name" "$file" || return 0
  speed "$name"
  audit_arm "$name" "${safe}_audittrap_skeptic_min"
  repo_arm  "$name" "${safe}_repohard_np24576"
}

screen_variant 'gemma4:31b-it-q8_0' 'gemma4-coding:31b-q8' \
  "$ROOT/modelfiles/gemma4-31b-coding-q8.Modelfile" 'gemma4-coding_31b-q8'
screen_variant 'gemma4:31b-mlx' 'gemma4-coding:31b-mlx' \
  "$ROOT/modelfiles/gemma4-31b-coding-mlx.Modelfile" 'gemma4-coding_31b-mlx'

echo
echo "======== STAGE E: everything, side by side ========"
"$PY" -u scripts/speed_variant_report.py || true
echo "==== ALL DONE mtp full and variants $(date)"
