#!/bin/zsh
# Re-run one poisoned cell: gemma4:31b think=medium BASE.
#
# The original run (15:12-16:48) decoded at 0.94-1.50 tok/s and lost two tickets
# to BENCH_TASK_TIMEOUT_S. The same model at the same think budget managed
# 6.76-8.27 tok/s immediately afterwards, and its think-off runs also sat at
# ~7.4 tok/s. A system prompt cannot slow decoding 5x, so that window was
# externally degraded (the laptop was suspended) and the 20/40 measures the
# suspension, not the model. Tagged _rerun so the poisoned result stays visible
# for comparison rather than being silently overwritten.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"

LOG="$ROOT/results/audittrap/gemma31b_fixmed_rerun.log"
PREV_LOG="$ROOT/results/audittrap/gemma_verify.log"
DONE_MARK='ALL DONE gemma verification'
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== gemma4 31b fix_med re-run $(date) ===="
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
GM='gemma4:31b-it-bf16'
have_model "$GM" || { echo "==== SKIP: $GM not installed ====" >&2; exit 3; }

# Identical to the original cell except the timeout, which is doubled so a slow
# stretch degrades the timing rather than voiding the score.
export BENCH_PROVIDER=ollama
export BENCH_TASKS='runner_interrupt_scored,chat_timeout_dropped,subprocess_stderr_dropped,warmup_no_deadline'
export BENCH_TEMPERATURE=0.1 BENCH_MAX_ROUNDS=40 BENCH_MAX_TOOL_CALLS=40
export BENCH_TASK_TIMEOUT_S=2400 BENCH_NUM_CTX=65536
export BENCH_KEEP_ALIVE=24h BENCH_NUM_PREDICT=24576
export BENCH_THINK=medium BENCH_THINK_MAX_CHARS=0
export BENCH_THINK_PROMOTE=1 BENCH_THINK_LOOP=1
export BENCH_SYSTEM_PROMPT=0
unset BENCH_SYSTEM_PROMPT_FILE BENCH_FINALIZE_AFTER BENCH_THINK_ROUNDS DS4_BASE

TAG='gemma4_31b-it-bf16_audittrap_fix_med_rerun'
echo "==== START $TAG $(date) ===="
BENCH_MODEL="$GM" BENCH_TAG="$TAG" "$PY" -u -m benches.audittrap || echo "==== FAILED $TAG ====" >&2
echo "==== DONE $TAG $(date) ===="
ollama stop "$GM" 2>/dev/null || true

"$PY" - <<'PY'
import json
from pathlib import Path

FIXES = [
    "runner_interrupt_scored",
    "chat_timeout_dropped",
    "subprocess_stderr_dropped",
    "warmup_no_deadline",
]
print("\n==== poisoned run vs clean re-run (gemma4 31b, think=medium, BASE) ====")
for label, tag in [
    ("original (suspended)", "gemma4_31b-it-bf16_audittrap_fix_med"),
    ("re-run", "gemma4_31b-it-bf16_audittrap_fix_med_rerun"),
]:
    p = Path(f"results/audittrap/{tag}_latest.json")
    if not p.exists():
        print(f"{label:22} missing")
        continue
    by = {r["task"]: r for r in json.loads(p.read_text())}
    pts = sum(int(by[t].get("score") or 0) for t in FIXES if t in by)
    wall = sum(float(by[t].get("wall_s") or 0) for t in FIXES if t in by)
    tok = sum(int(by[t].get("eval_tokens") or 0) for t in FIXES if t in by)
    outs = sum(1 for t in FIXES if t in by and "TIMEOUT" in str(by[t].get("grade_detail") or ""))
    rate = tok / wall if wall else 0
    print(f"{label:22} {pts:2}/40  {wall/60:5.1f} min  {rate:5.2f} tok/s  {outs} timeouts")
print("==== ALL DONE gemma31b fixmed re-run", flush=True)
PY
