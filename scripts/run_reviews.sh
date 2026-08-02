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

# A detached worktree at HEAD, not the repository itself: a stage runs with permission prompts off,
# and a review has no business touching somebody's uncommitted work. Rebuilt when missing so the
# review leaves nothing behind in the tree it reviewed.
assay_tree() {
  local src="$HOME/Projects/assay" tree=/tmp/assay-review
  [[ -d "$tree" ]] && return 0
  git -C "$src" worktree add --detach "$tree" HEAD >/dev/null 2>&1 \
    || { echo "cannot make a worktree of $src" >&2; return 1; }
}

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

# Scoped to one subsystem on purpose. This repository is larger than the context window, and the
# first attempt proved what that costs: the survey read the first 500 lines of each long file, and
# the claims stage then cited line 2619 of a file it had seen to line 500 -- inventing the code
# there. A review of everything is a review of nothing; the trade path is where a defect is
# expensive.
ASSAY_TASK='In-depth architecture and code review of the trade execution path of this repository: tools/trade_service.py, tools/ibkr_trade.py, tools/rebalance.py and the web/src/trade.ts surface that drives them. Read ARCHITECTURE.md first for the invariants the code is meant to hold. These files are long; find what you need with rg and read around the hit rather than reading from the top. Find the defects that matter: logic that is wrong rather than merely ugly, an order or a fill that can be lost or double-counted, state that can go inconsistent between preview and placement, failure modes that pass silently, and invariants ARCHITECTURE.md states that the code does not keep. For each defect, name the change you would make.'

case "${1:-both}" in
  self)  review self  "$REPO"           "$SELF_TASK" ;;
  assay) assay_tree && review assay /tmp/assay-review "$ASSAY_TASK" ;;
  both)  review self  "$REPO"           "$SELF_TASK"
         assay_tree && review assay /tmp/assay-review "$ASSAY_TASK" ;;
  *)     echo "usage: $0 [self|assay|both]" >&2; exit 2 ;;
esac

echo "=== done at $(date +%T); artifacts in $OUT_ROOT ===" | tee -a "$OUT_ROOT/run.log"
