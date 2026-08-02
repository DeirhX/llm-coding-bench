#!/usr/bin/env bash
# Drive the depth pipeline over two repositories, one after the other.
#
# Sequential by necessity, not by preference: one 31B runner fits in 128 GB and the pipeline's own
# lock refuses a second driver. Each review writes into this repo rather than the repo under review,
# so a review of somebody else's tree leaves no trace in it.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/.venv/bin/python"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT_ROOT="$REPO/artifacts/reviews/$STAMP"
mkdir -p "$OUT_ROOT"

review() {
  local name="$1" cwd="$2" task="$3"
  local out="$OUT_ROOT/$name"
  mkdir -p "$out"
  echo "=== $name against $cwd ===" | tee -a "$OUT_ROOT/run.log"
  "$PY" "$REPO/scripts/depth_pipeline.py" "$task" \
      --adapter review --cwd "$cwd" --out "$out" --yolo \
      >>"$OUT_ROOT/run.log" 2>&1
  echo "--- $name exited $? at $(date +%T) ---" | tee -a "$OUT_ROOT/run.log"
}

SELF_TASK='In-depth architecture and code review of the depth-enforcement machinery in this repository: scripts/cc_ledger.py, scripts/cc-depth-gate.py, scripts/cc_verify.py, scripts/cc_evidence.py, scripts/depth_pipeline.py, scripts/anthropic_proxy.py and bench_lib/. Find the defects that matter: code that does not do what its docstring claims, failure modes that pass silently instead of erroring, evidence checks an answer can satisfy while still being wrong, and structural choices that will break when a second adapter or a second backend is added. For each defect, name the change you would make.'

ASSAY_TASK='In-depth architecture and code review of this repository. Start from ARCHITECTURE.md and ORIENTATION.md to learn what the system is meant to be, then read the code under web/ and tools/ and judge whether it matches. Find the defects that matter: logic that is wrong rather than merely ugly, failure modes that pass silently, state that can go inconsistent, boundaries the code crosses that the architecture says it should not, and duplication that will drift. For each defect, name the change you would make.'

review self  "$REPO"            "$SELF_TASK"
review assay /tmp/assay-review  "$ASSAY_TASK"

echo "=== done at $(date +%T); artifacts in $OUT_ROOT ===" | tee -a "$OUT_ROOT/run.log"
