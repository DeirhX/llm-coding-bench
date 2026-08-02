#!/bin/zsh
# Can either gemma be used for interactive coding, where none of this harness's
# rescues exist? Every arm here runs with BENCH_REALISM=1, which disables the
# think-loop detector, the nudge-and-retry, and think-to-answer promotion. Nothing
# watches the stream, nothing re-prompts, nothing salvages an answer left behind in
# the thinking channel -- the same deal a client gives the model.
#
# Every arm also runs BENCH_TEMPERATURE=auto, meaning the bench sends NO temperature
# and Ollama falls back to the model's own Modelfile. That matters more than it
# sounds: gemma4:31b ships temperature 1 and gemma4:26b ships no sampler at all,
# inheriting Ollama's 0.8, while every score in this repo was measured at 0.1 sent
# explicitly by the bench. The shipped configuration is the untested one.
#
# The two tuned models pin the tested sampler and add the two fixes that a client
# cannot supply for itself:
#   temperature 0.1        the value everything here was measured at
#   num_predict 8192       bounds the 26B's plain-text deliberation runaway
#   stop <arch_result>     stops the 26B fabricating the harness half of the protocol
#
# repohard is the interactive proxy: multi-file, tool-driven, 8 tasks. audittrap adds
# the false-bug traps, where both models scored 0/20, paired with the system prompt
# that names the "unchanged" status token -- a system prompt IS something a client can
# set, so unlike a loop detector it is a legitimate fix.
#
# One caveat this cannot measure: the bench still sends num_predict 8192 even on the
# stock arms, so the stock cells are NOT exposed to Ollama's unbounded default. A real
# client that sends no num_predict gives the 26B unlimited rope for the deliberation
# runaway, which is a hung request rather than a low score. That risk is argued from
# the 24,576-token round already observed, not measured here.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"

LOG="$ROOT/results/gemma_interactive_realism.log"
PREV_LOG="$ROOT/results/repohard_gemma26_stop_arms.log"
DONE_MARK='ALL DONE gemma26 stop arms'
SYS='benches/audittrap/system_local.md'
mkdir -p results
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== gemma interactive-realism arms $(date) ===="
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
  local model="$1" bench="$2" tag="$3" sysp="$4" note="$5"
  if ! have_model "$model"; then
    echo "---- SKIP $tag: $model not installed ----" >&2
    return 0
  fi
  echo
  echo "---- START $tag model=$model bench=$bench sysprompt=${sysp:-none} $(date) ----"
  echo "     $note"
  (
    export BENCH_PROVIDER=ollama
    export BENCH_REALISM=1
    export BENCH_TEMPERATURE=auto
    export BENCH_THINK=0
    export BENCH_MAX_ROUNDS=40 BENCH_MAX_TOOL_CALLS=40
    export BENCH_TASK_TIMEOUT_S=1200 BENCH_NUM_CTX=65536
    export BENCH_KEEP_ALIVE=24h
    # no harness rescue of any kind, and no finalize nudge either: that is a rescue too
    unset BENCH_NUM_PREDICT BENCH_FINALIZE_AFTER BENCH_THINK_MAX_CHARS || true
    unset BENCH_THINK_LOOP BENCH_THINK_PROMOTE BENCH_STOP_FABRICATION || true
    if [[ -n "$sysp" ]]; then
      export BENCH_SYSTEM_PROMPT=1 BENCH_SYSTEM_PROMPT_FILE="$sysp"
    else
      export BENCH_SYSTEM_PROMPT=0
      unset BENCH_SYSTEM_PROMPT_FILE || true
    fi
    BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" -u -m "benches.$bench"
  ) || echo "---- FAILED $tag rc=$? $(date) ----" >&2
  echo "---- DONE $tag $(date) ----"
  ollama stop "$model" 2>/dev/null || true
}

G26_STOCK='gemma4:26b-a4b-it-bf16'
G26_TUNED='gemma4-coding:26b-a4b'
G31_STOCK='gemma4:31b-it-bf16'
G31_TUNED='gemma4-coding:31b'

# repohard first: the closest thing here to interactive agent work
run_arm "$G31_TUNED" repohard 'gemma4-coding_31b_repohard_realism'       '' \
  'G4: the recommendation. Tuned modelfile, no rescues. Reference is 77/80 at temp 0.1 with rescues on.'
run_arm "$G31_STOCK" repohard 'gemma4_31b_repohard_realism_shipped'      '' \
  'G3: shipped sampler (temperature 1). Does 77/80 survive the config a client actually gets?'
run_arm "$G26_TUNED" repohard 'gemma4-coding_26b-a4b_repohard_realism'   '' \
  'G2: tuned. Does stopping the fabricated tool dialogue fix the 97-minute run?'
run_arm "$G26_STOCK" repohard 'gemma4_26b-a4b_repohard_realism_shipped'  '' \
  'G1: shipped sampler (Ollama default 0.8, no stop). What an interactive user gets today.'

# then the traps, with the one fix a client can legitimately apply
run_arm "$G31_TUNED" audittrap 'gemma4-coding_31b_audittrap_realism_syslocal'     "$SYS" \
  'G6: full 60-point suite. Does naming the unchanged token fix 0/20 trap discipline?'
run_arm "$G26_TUNED" audittrap 'gemma4-coding_26b-a4b_audittrap_realism_syslocal' "$SYS" \
  'G5: same question for the sparse model, which rejects false claims but patches non-bugs.'

"$PY" - <<'PY'
import json
from pathlib import Path

FIXES = [
    "runner_interrupt_scored",
    "chat_timeout_dropped",
    "subprocess_stderr_dropped",
    "warmup_no_deadline",
]
TRAPS = ["sat_assign_cleared", "sql_where_inside_join"]

REPOHARD = [
    ("31B tuned, no rescues", "gemma4-coding_31b_repohard_realism"),
    ("31B shipped sampler", "gemma4_31b_repohard_realism_shipped"),
    ("31B reference (temp 0.1, rescues on)", "gemma4_31b-it-bf16_repohard"),
    ("26B tuned, no rescues", "gemma4-coding_26b-a4b_repohard_realism"),
    ("26B shipped sampler", "gemma4_26b-a4b_repohard_realism_shipped"),
    ("26B reference (temp 0.1, rescues on)", "gemma4_26b-a4b-it-bf16_repohard"),
]

print()
print("==== repohard without harness rescues: is either gemma deployable? ====")
header = f"{'arm':38}{'score':>8}{'tok/task':>10}{'wall/task':>11}{'unclean':>9}{'fake results':>14}"
print(header)
print("-" * len(header))
for label, tag in REPOHARD:
    p = Path(f"results/repohard/{tag}_latest.json")
    if not p.exists():
        print(f"{label:38}{'not run':>8}")
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
    print(f"{label:38}{f'{sc}/{mx}':>8}{tok / n:>10.0f}{f'{wall / n:.0f}s':>11}{bad:>9}{fake:>14}")

AUDIT = [
    ("31B tuned + system_local", "gemma4-coding_31b_audittrap_realism_syslocal"),
    ("31B BASE reference", "gemma4_31b-it-bf16_audittrap_full_base"),
    ("26B tuned + system_local", "gemma4-coding_26b-a4b_audittrap_realism_syslocal"),
    ("26B BASE reference", "gemma4_26b-a4b-it-bf16_audittrap_verify_full"),
]
print()
print("==== audittrap full suite: does a system prompt buy trap discipline? ====")
header = f"{'arm':30}{'repairs':>10}{'traps':>8}{'FULL':>9}"
print(header)
print("-" * len(header))
for label, tag in AUDIT:
    p = Path(f"results/audittrap/{tag}_latest.json")
    if not p.exists():
        print(f"{label:30}{'not run':>10}")
        continue
    by = {r["task"]: r for r in json.loads(p.read_text())}
    fg = sum(int(by[t].get("score") or 0) for t in FIXES if t in by)
    tg = sum(int(by[t].get("score") or 0) for t in TRAPS if t in by)
    has_traps = all(t in by for t in TRAPS)
    print(
        f"{label:30}{f'{fg}/40':>10}"
        f"{(f'{tg}/20' if has_traps else 'n/a'):>8}{(f'{fg + tg}/60' if has_traps else 'n/a'):>9}"
    )

print()
print("Read the repohard table by column, not by score alone. 'fake results' going to")
print("zero proves the stop sequence works; 'unclean' going to zero proves the request")
print("would have returned to a user at all. A model can be deployable and still weak.")
print("==== ALL DONE gemma interactive realism", flush=True)
PY
