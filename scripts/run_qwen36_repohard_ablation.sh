#!/bin/zsh
# Ablate harness knobs on qwen3.6 repohard soft/failed tasks.
# Baseline scores are taken from the completed full-suite latest (not re-run).
#
# Soft tasks: race (7), migration/n+1/deputy/client (0).
# Variants: think_medium, ctx128k, rounds80, predict24k, finalize_r20.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"
MODEL="qwen3.6:35b-a3b-coding-bf16"
TASKS="race_webhook_idempotency,migration_backfill_hole,nplus1_reconciliation,confused_deputy_admin,client_contract_drift"
OUT="$ROOT/results/repohard/ablation_qwen36"
LOG="$OUT/ablation.log"
mkdir -p "$OUT"
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== qwen3.6 repohard ablation start $(date) ===="
echo "tasks=$TASKS"

# Snapshot baseline from existing full suite
"$PY" - <<'PY'
import json
from pathlib import Path
src = Path("results/repohard/qwen3.6_35b-a3b-coding-bf16_repohard_latest.json")
tasks = {
    "race_webhook_idempotency",
    "migration_backfill_hole",
    "nplus1_reconciliation",
    "confused_deputy_admin",
    "client_contract_drift",
}
rows = [r for r in json.loads(src.read_text()) if r.get("task") in tasks]
out = Path("results/repohard/ablation_qwen36/baseline_soft_tasks.json")
out.write_text(json.dumps(rows, indent=2) + "\n")
s = sum(int(r.get("score") or 0) for r in rows)
m = sum(int(r.get("max_score") or 0) for r in rows)
print(f"baseline soft tasks: {s}/{m} n={len(rows)}")
for r in rows:
    print(f"  {r['task']}: {r['score']}/{r.get('max_score')} rounds={r.get('rounds')} detail={str(r.get('grade_detail') or '')[:60]}")
PY

run_variant() {
  local name="$1"
  shift
  echo "==== variant $name start $(date) ===="
  git -C "$ROOT" checkout -- benches/repohard/fixture/ledgerkit/ || true
  export BENCH_PROVIDER=ollama
  export BENCH_MODEL="$MODEL"
  export BENCH_TASKS="$TASKS"
  export BENCH_OUT="$ROOT/results"
  export BENCH_TAG="qwen36_ablate_${name}"
  export BENCH_THINK=0
  export BENCH_NUM_CTX=65536
  export BENCH_MAX_ROUNDS=40
  export BENCH_MAX_TOOL_CALLS=40
  export BENCH_NUM_PREDICT=8192
  export BENCH_FINALIZE_AFTER=0
  export BENCH_TASK_TIMEOUT_S=900
  # apply overrides from remaining args KEY=VAL
  local kv
  for kv in "$@"; do
    export "$kv"
  done
  echo "env THINK=$BENCH_THINK CTX=$BENCH_NUM_CTX ROUNDS=$BENCH_MAX_ROUNDS PREDICT=$BENCH_NUM_PREDICT FINALIZE_AFTER=$BENCH_FINALIZE_AFTER TIMEOUT=$BENCH_TASK_TIMEOUT_S"
  if ! "$PY" -u "$ROOT/run.py" run repohard; then
    echo "WARN variant $name failed rc=$?"
  fi
  # copy latest into ablation folder
  local latest="$ROOT/results/repohard/${BENCH_TAG}_latest.json"
  if [[ -f "$latest" ]]; then
    cp "$latest" "$OUT/${name}_latest.json"
    "$PY" - <<PY
import json
from pathlib import Path
rows = json.loads(Path("$latest").read_text())
s = sum(int(r.get("score") or 0) for r in rows)
m = sum(int(r.get("max_score") or 0) for r in rows)
print(f"variant $name: {s}/{m} n={len(rows)}")
for r in rows:
    print(f"  {r['task']}: {r['score']}/{r.get('max_score')} rounds={r.get('rounds')} empty_patch={str(r.get('grade_detail') or '').startswith('patch_apply: empty')} detail={str(r.get('grade_detail') or '')[:70]}")
PY
  else
    echo "WARN no latest for $name"
  fi
  echo "==== variant $name done $(date) ===="
}

# Order: cheapest/most informative first; think last (slow).
run_variant predict24k BENCH_NUM_PREDICT=24576
run_variant ctx128k BENCH_NUM_CTX=131072
run_variant rounds80 BENCH_MAX_ROUNDS=80 BENCH_MAX_TOOL_CALLS=80
run_variant finalize_r20 BENCH_FINALIZE_AFTER=20
run_variant think_medium BENCH_THINK=medium BENCH_NUM_PREDICT=24576 BENCH_TASK_TIMEOUT_S=1200

echo "==== writing comparison ===="
"$PY" - <<'PY'
import json
from pathlib import Path

out = Path("results/repohard/ablation_qwen36")
tasks = [
    "race_webhook_idempotency",
    "migration_backfill_hole",
    "nplus1_reconciliation",
    "confused_deputy_admin",
    "client_contract_drift",
]
variants = ["baseline"] + [
    p.stem.replace("_latest", "")
    for p in sorted(out.glob("*_latest.json"))
]
# baseline from snapshot
data = {}
base = json.loads((out / "baseline_soft_tasks.json").read_text())
data["baseline"] = {r["task"]: r for r in base}
for p in sorted(out.glob("*_latest.json")):
    name = p.name.replace("_latest.json", "")
    rows = json.loads(p.read_text())
    data[name] = {r["task"]: r for r in rows}

# table
header = ["task"] + list(data.keys())
lines = [" | ".join(header), " | ".join(["---"] * len(header))]
totals = {v: 0 for v in data}
for t in tasks:
    row = [t]
    for v in data:
        r = data[v].get(t)
        sc = int(r.get("score") or 0) if r else -1
        totals[v] += max(sc, 0)
        row.append(str(sc) if sc >= 0 else "—")
    lines.append(" | ".join(row))
lines.append(" | ".join(["TOTAL"] + [str(totals[v]) for v in data]))
md = "# qwen3.6 repohard ablation (soft tasks /50)\n\n" + "\n".join(lines) + "\n"
# delta vs baseline
base_total = totals["baseline"]
md += "\n## Delta vs baseline\n\n"
for v, tot in totals.items():
    if v == "baseline":
        continue
    md += f"- **{v}**: {tot}/50 ({tot - base_total:+d} vs baseline {base_total})\n"
(out / "COMPARISON.md").write_text(md)
print(md)
PY

echo "==== qwen3.6 repohard ablation ALL DONE $(date) ===="
