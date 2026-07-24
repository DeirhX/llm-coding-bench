#!/bin/zsh
# Cursor-side post-harness refresh that is safe to run beside the local Ollama
# universal matrix.
#
# Safe in parallel: claim/arch/pyhard (Cursor cloud; no shared ledgerkit mutate).
# Deferred: repohard — waits until no local `run.py run repohard` is alive.
#
# Also offline-rescored claim zeros (parser fix) + arch evidence rescores.
set -uo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"
OUT="$ROOT/results/cursor_remote_refresh"
LOG="$OUT/remote.log"
mkdir -p "$OUT"
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== cursor remote refresh start $(date) ===="
export BENCH_PROVIDER=cursor
export BENCH_OUT="$ROOT/results"
export BENCH_CURSOR_MODE="${BENCH_CURSOR_MODE:-ask}"
export BENCH_MERGE_LATEST=0
unset BENCH_TAG BENCH_TASKS BENCH_THINK BENCH_THINK_MAX_CHARS

echo "---- offline: rescore claim zeros / parse failures ----"
"$PY" -u - <<'PY'
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from benches.claim.bench import parse_final, grade_answers
from benches.shopapi.tools import ToolSession

arch = Path("results/archbench")
n = 0
for p in sorted(arch.glob("cursor_*_claim_latest.json")):
    d = json.loads(p.read_text(encoding="utf-8"))
    raw = d.get("raw_content") or ""
    if not raw or '"answers"' not in raw:
        continue
    if d.get("answer") is not None and int(d.get("missing") or 0) == 0:
        continue
    final = parse_final(raw)
    if final is None:
        print(f"SKIP parse still fail {p.name}")
        continue
    g = grade_answers(final, ToolSession(max_calls=40))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bak = p.with_name(p.name.replace("_latest.json", f"_pre_rescore_{stamp}.json"))
    bak.write_text(json.dumps(d, indent=2) + "\n", encoding="utf-8")
    out = dict(d)
    out.update(g)
    out["answer"] = final
    out["rescored_from_raw"] = True
    out["rescored_at"] = stamp
    # drop huge per_claim duplication noise already in g
    p.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"RESCORDED {p.name}: {g.get('detail')} score={g.get('score')}/{g.get('max_score')} (bak {bak.name})")
    n += 1
print(f"claim_rescore_n={n}")
PY

echo "---- offline: arch evidence rescore (all cursor latest) ----"
"$PY" -u -m benches.arch.rescore results/archbench/cursor_*_arch_latest.json || echo "WARN arch rescore rc=$?"

# Models whose claim is still garbage after offline rescore → cloud re-run (safe).
echo "---- cloud: claim re-run only if still broken ----"
"$PY" -u - <<'PY'
import json, os, subprocess, sys
from pathlib import Path

root = Path(".").resolve()
py = os.environ.get("BENCH_PYTHON") or str(root / ".venv/bin/python")
need = []
for p in sorted(Path("results/archbench").glob("cursor_*_claim_latest.json")):
    d = json.loads(p.read_text(encoding="utf-8"))
    missing = int(d.get("missing") or 0)
    correct = int(d.get("correct") or 0)
    if missing >= 10 or (correct == 0 and (d.get("max_score") or 0) >= 20):
        # tag: cursor_<model>_claim_latest → model is between cursor_ and _claim
        name = p.name.removeprefix("cursor_").removesuffix("_claim_latest.json")
        # model ids use dots; our safe names keep them
        need.append(name.replace("_", "-") if False else name)
models = list(dict.fromkeys(need))  # filenames already use model id with dots
print("claim_rerun_models", models)
Path("results/cursor_remote_refresh/claim_rerun_models.txt").write_text(
    "\n".join(models) + ("\n" if models else ""), encoding="utf-8"
)
PY

while IFS= read -r model || [[ -n "${model:-}" ]]; do
  [[ -z "${model:-}" ]] && continue
  safe="$(echo "$model" | sed 's/[^a-zA-Z0-9._-]/_/g')"
  echo "==== CLAIM re-run model=$model $(date) ===="
  BENCH_MODEL="$model" BENCH_TAG="cursor_${safe}_claim" \
    "$PY" -u "$ROOT/run.py" run claim || echo "WARN claim failed model=$model"
done <"$OUT/claim_rerun_models.txt"

# Repohard would race the local matrix (shared ledgerkit + ollama mutates fixture).
# Only wait/run if someone listed models in repohard_models.txt.
REPO_LIST="$OUT/repohard_models.txt"
if [[ -s "$REPO_LIST" ]]; then
  echo "---- wait for local repohard idle before Cursor repohard ----"
  for i in $(seq 1 2000); do
    if ! pgrep -f '/run.py run repohard' >/dev/null 2>&1; then
      echo "local repohard idle $(date)"
      break
    fi
    sleep 30
  done
  while IFS= read -r model || [[ -n "${model:-}" ]]; do
    [[ -z "${model:-}" ]] && continue
    safe="$(echo "$model" | sed 's/[^a-zA-Z0-9._-]/_/g')"
    echo "==== REPOHARD model=$model $(date) ===="
    git -C "$ROOT" checkout -- benches/repohard/fixture/ledgerkit/ 2>/dev/null || true
    BENCH_MODEL="$model" BENCH_TAG="cursor_${safe}_repohard" \
      "$PY" -u "$ROOT/run.py" run repohard || echo "WARN repohard failed model=$model"
  done <"$REPO_LIST"
else
  echo "---- skip Cursor repohard (no $REPO_LIST; avoids race with local matrix) ----"
fi

echo "---- report ----"
"$PY" -u "$ROOT/run.py" report --no-color || true
echo "==== cursor remote refresh ALL DONE $(date) ===="
