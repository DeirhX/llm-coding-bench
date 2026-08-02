#!/bin/zsh
# Qwopus at BASE: 38/40 on repairs, 0/20 on the traps -> 38/60 on the full suite.
# The two trap failures have different causes and only one is eagerness:
#   sql_where_inside_join  patched solver/sql.py, which must be left alone
#   sat_assign_cleared     returned status='' after burning all 40 rounds
#
# The base preamble already documents the escape hatch verbatim, with a worked
# example: {"status": "unchanged", "reason": "...", "citations": [...]}. So the
# model is told the token exists and still patches a non-bug. What the older
# system prompt adds is emphasis, not information:
#
#   system_local.md      "choose status 'unchanged' ... do not invent a fix" and
#                        "Do not 'fix' unused/legacy modules when the live path is
#                        correct. Prefer 'unchanged' over a speculative patch."
#   system_local_general "leave the tree alone and say why" -- never names the token
#
# That distinction tracks the historical scores: the sysprompt-tagged runs (older,
# system_local.md family) hit 20/20 on traps for qwen3.6 and qwen3-coder while the
# sysgen runs managed 10/20 and 0/20. Every local model at BASE sits at 0/20,
# including deepseek-v4-flash at 38/40 repairs, so this is a family-wide pattern
# rather than a Qwopus quirk.
#
# One arm, full suite, think=medium (the only budget where Qwopus terminates).
# Success is trap points appearing at all, and NOT losing repair points to do it --
# for qwen3.6 and qwen3.5 the prompt was nearly free on repairs.
#
# Note a provenance gap this exposed: result JSON records system_prompt as a bare
# boolean and never which file, so the historical 20/20 runs cannot be attributed
# to a specific prompt with certainty. Worth fixing alongside the sampler
# provenance work.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"

LOG="$ROOT/results/audittrap/qwopus_trap_prompt.log"
PREV_LOG="$ROOT/results/weak_cell_rescue.log"
DONE_MARK='ALL DONE weak-cell rescue'
QW='qwopus3.6:35b-a3b-coder-q8_0'
SYS='benches/audittrap/system_local.md'
mkdir -p results/audittrap
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== qwopus trap-prompt arm $(date) ===="
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
if ! have_model "$QW"; then
  echo "==== SKIP: $QW not installed $(date) ====" >&2
  echo "==== ALL DONE qwopus trap-prompt arm"
  exit 0
fi
if [[ ! -f "$SYS" ]]; then
  echo "==== SKIP: $SYS missing $(date) ====" >&2
  echo "==== ALL DONE qwopus trap-prompt arm"
  exit 0
fi

export BENCH_PROVIDER=ollama
export BENCH_TEMPERATURE=0.1
export BENCH_MAX_ROUNDS=40 BENCH_MAX_TOOL_CALLS=40
export BENCH_TASK_TIMEOUT_S=1200 BENCH_NUM_CTX=65536
export BENCH_KEEP_ALIVE=24h
export BENCH_THINK=medium BENCH_THINK_MAX_CHARS=0
export BENCH_THINK_PROMOTE=1 BENCH_THINK_LOOP=1
export BENCH_SYSTEM_PROMPT=1 BENCH_SYSTEM_PROMPT_FILE="$SYS"
# never pin num_predict: audittrap raises its own default when thinking
unset BENCH_NUM_PREDICT BENCH_TASKS BENCH_FINALIZE_AFTER DS4_BASE || true

TAG='qwopus3.6_35b-a3b-coder-q8_0_audittrap_full_med_syslocal'
echo "==== START $TAG full suite, system_local.md $(date) ===="
BENCH_MODEL="$QW" BENCH_TAG="$TAG" "$PY" -u -m benches.audittrap || \
  echo "==== FAILED $TAG rc=$? $(date) ====" >&2
echo "==== DONE $TAG $(date) ===="
ollama stop "$QW" 2>/dev/null || true

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

ARMS = [
    ("BASE, no system prompt", "qwopus3.6_35b-a3b-coder-q8_0_audittrap_full_med"),
    ("system_local.md", "qwopus3.6_35b-a3b-coder-q8_0_audittrap_full_med_syslocal"),
]

print()
print("==== does naming the 'unchanged' token buy trap discipline? ====")
header = f"{'arm':26}{'repairs':>10}{'traps':>8}{'FULL':>9}{'wall':>9}  per-trap outcome"
print(header)
print("-" * len(header))

for label, tag in ARMS:
    p = Path(f"results/audittrap/{tag}_latest.json")
    if not p.exists():
        print(f"{label:26}{'not run':>10}")
        continue
    by = {r["task"]: r for r in json.loads(p.read_text())}
    fg = sum(int(by[t].get("score") or 0) for t in FIXES if t in by)
    fm = sum(int(by[t].get("max_score") or 0) for t in FIXES if t in by)
    tg = sum(int(by[t].get("score") or 0) for t in TRAPS if t in by)
    tm = sum(int(by[t].get("max_score") or 0) for t in TRAPS if t in by)
    wall = sum(float(by[t].get("wall_s") or 0) for t in FIXES + TRAPS if t in by)
    bits = []
    for t in TRAPS:
        r = by.get(t)
        if not r:
            continue
        detail = str(r.get("grade_detail") or r.get("detail") or "")[:34]
        bits.append(f"{t.split('_')[0]}={r.get('score')}/{r.get('max_score')} ({detail})")
    print(
        f"{label:26}{f'{fg}/{fm}':>10}{f'{tg}/{tm}':>8}"
        f"{f'{fg + tg}/{fm + tm}':>9}{f'{wall:.0f}s':>9}  {'; '.join(bits)}"
    )

print()
print("Success is trap points appearing WITHOUT losing repair points -- for qwen3.6")
print("and qwen3.5 the prompt was nearly free on repairs (10/40 and 20/40 either way).")
print("Report audittrap out of 60 from now on: the 40/40 that made Qwopus look best")
print("on this bench only ever ran the four repair tickets.")
print("==== ALL DONE qwopus trap-prompt arm", flush=True)
PY
