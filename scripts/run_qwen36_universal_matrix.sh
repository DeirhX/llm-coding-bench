#!/bin/zsh
# Universal-config probe matrix for qwen3.6.
# Same sticky knobs across repohard + arch + pyhard (subset tasks).
# Score: mean of suite% and min(suite%) — "universal" must not nuke one class.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"
OUT="$ROOT/results/universal_matrix"
LOG="$OUT/matrix.log"
mkdir -p "$OUT"
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== universal matrix start $(date) ===="

export BENCH_PROVIDER=ollama
export BENCH_MODEL='qwen3.6:35b-a3b-coding-bf16'
export BENCH_NUM_CTX=65536
export BENCH_NUM_PREDICT=24576
export BENCH_TEMPERATURE=0.1
export BENCH_TASK_TIMEOUT_S=1200
export BENCH_OUT="$ROOT/results"
export BENCH_MERGE_LATEST=0
export BENCH_THINK_PROMOTE=1
export BENCH_THINK_LOOP=1
unset BENCH_THINK_ROUNDS

# Discriminating subsets (not full suites — screen then promote winners).
REPO_TASKS='money_rounding_split,outbox_poison_retry,race_webhook_idempotency,confused_deputy_admin,nplus1_reconciliation'
ARCH_TASKS='chain_delete_order,chain_payment_webhook,tenant_invoice_isolation,redesign_webhook_idempotency,invariant_doc_vs_code'
PY_TASKS='regex_match,lru_cache,alien_order,eval_expr,fix_vm'

# max scores for probe subsets
REPO_MAX=50
ARCH_MAX=50
PY_MAX=58

run_variant() {
  local name="$1"
  shift
  # remaining args are KEY=VAL exports
  echo "######## VARIANT $name $(date) ########"
  unset BENCH_THINK BENCH_THINK_MAX_CHARS BENCH_FINALIZE_AFTER BENCH_TAG BENCH_TASKS
  export BENCH_THINK=0
  export BENCH_THINK_MAX_CHARS=0
  export BENCH_FINALIZE_AFTER=0
  export BENCH_NUM_PREDICT=24576
  for kv in "$@"; do
    export "$kv"
  done
  local tag="qwen36_univ_${name}"
  echo "env THINK=$BENCH_THINK MAX_CHARS=$BENCH_THINK_MAX_CHARS FINALIZE=$BENCH_FINALIZE_AFTER PREDICT=$BENCH_NUM_PREDICT"

  git -C "$ROOT" checkout -- benches/repohard/fixture/ledgerkit/ 2>/dev/null || true

  # Tags omit suite suffix; pyhard harness appends `_pyhard_latest.json`.
  export BENCH_TAG="${tag}_rh" BENCH_TASKS="$REPO_TASKS"
  echo "-- repohard $name --"
  "$PY" -u "$ROOT/run.py" run repohard || echo "WARN repohard rc=$?"

  export BENCH_TAG="${tag}_ar" BENCH_TASKS="$ARCH_TASKS"
  echo "-- arch $name --"
  "$PY" -u "$ROOT/run.py" run arch || echo "WARN arch rc=$?"

  export BENCH_TAG="${tag}_ph" BENCH_TASKS="$PY_TASKS"
  echo "-- pyhard $name --"
  "$PY" -u "$ROOT/run.py" run pyhard || echo "WARN pyhard rc=$?"

  "$PY" -u "$OUT/score_matrix.py" --variant "$name" \
    --repo "$ROOT/results/repohard/${tag}_rh_latest.json" \
    --arch "$ROOT/results/archbench/${tag}_ar_latest.json" \
    --pyhard "$ROOT/results/${tag}_ph_pyhard_latest.json" \
    --repo-max "$REPO_MAX" --arch-max "$ARCH_MAX" --py-max "$PY_MAX" \
    --out "$OUT/scores.jsonl" || true
  echo "######## END $name $(date) ########"
}

# Score helper written next to this script's OUT
cat >"$OUT/score_matrix.py" <<'PY'
#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path

def walk_scores(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    d = json.loads(path.read_text())
    by = {}
    def walk(x):
        if isinstance(x, dict):
            if "score" in x and "task" in x:
                by[x["task"]] = x
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(d)
    if by:
        s = sum(int(t.get("score") or 0) for t in by.values())
        m = sum(int(t.get("max_score") or 0) for t in by.values())
        return s, m or 0
    if isinstance(d, list):
        s = sum(int(t.get("score") or 0) for t in d if isinstance(t, dict))
        m = sum(int(t.get("max_score") or 0) for t in d if isinstance(t, dict))
        return s, m
    if isinstance(d, dict) and "score" in d:
        return int(d["score"]), int(d.get("max_score") or 0)
    return 0, 0

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--arch", required=True)
    ap.add_argument("--pyhard", required=True)
    ap.add_argument("--repo-max", type=int, required=True)
    ap.add_argument("--arch-max", type=int, required=True)
    ap.add_argument("--py-max", type=int, required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    rs, rm = walk_scores(Path(args.repo))
    as_, am = walk_scores(Path(args.arch))
    ps, pm = walk_scores(Path(args.pyhard))
    # Prefer declared probe maxima when file max incomplete
    rm = args.repo_max if rm < args.repo_max else rm
    am = args.arch_max if am < args.arch_max else am
    pm = args.py_max if pm < args.py_max else pm
    rp, apct, pp = (100 * rs / rm if rm else 0), (100 * as_ / am if am else 0), (100 * ps / pm if pm else 0)
    mean = (rp + apct + pp) / 3
    worst = min(rp, apct, pp)
    row = {
        "variant": args.variant,
        "repo": f"{rs}/{rm}",
        "arch": f"{as_}/{am}",
        "pyhard": f"{ps}/{pm}",
        "repo_pct": round(rp, 1),
        "arch_pct": round(apct, 1),
        "py_pct": round(pp, 1),
        "universal_mean": round(mean, 1),
        "universal_min": round(worst, 1),
    }
    out = Path(args.out)
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    print("SCORE", json.dumps(row))

if __name__ == "__main__":
    main()
PY

# --- Variants: every sticky idea worth burning GPU on ---
# 1) Think-off baseline (recommended earlier)
run_variant off \
  BENCH_THINK=0 BENCH_THINK_MAX_CHARS=0

# 2) Low think, no char guillotine (main hope for universal+)
run_variant low \
  BENCH_THINK=low BENCH_THINK_MAX_CHARS=0

# 3) Medium think, no char guillotine + promote/loop
run_variant med \
  BENCH_THINK=medium BENCH_THINK_MAX_CHARS=0

# 4) Low + soft 16k think cap (emit room?)
run_variant low_c16k \
  BENCH_THINK=low BENCH_THINK_MAX_CHARS=16384

# 5) Medium + 16k cap (relax vs failed 8k)
run_variant med_c16k \
  BENCH_THINK=medium BENCH_THINK_MAX_CHARS=16384

# 6) Medium + 24k cap (= predict-sized think headroom)
run_variant med_c24k \
  BENCH_THINK=medium BENCH_THINK_MAX_CHARS=24576

# 7) High think, no cap (probably worse loops; must check)
run_variant high \
  BENCH_THINK=high BENCH_THINK_MAX_CHARS=0

# 8) Low + finalize nudge for agent suites
run_variant low_fin15 \
  BENCH_THINK=low BENCH_THINK_MAX_CHARS=0 BENCH_FINALIZE_AFTER=15

# 9) Medium + finalize
run_variant med_fin15 \
  BENCH_THINK=medium BENCH_THINK_MAX_CHARS=0 BENCH_FINALIZE_AFTER=15

# 10) Boolean think=true (not a level) — may differ from medium
run_variant think_true \
  BENCH_THINK=1 BENCH_THINK_MAX_CHARS=0

# 11) Off but tighter predict (faster pure coding?)
run_variant off_p16k \
  BENCH_THINK=0 BENCH_THINK_MAX_CHARS=0 BENCH_NUM_PREDICT=16384

# 12) Low + 8k (expect pyhard death; confirms floor)
run_variant low_c8k \
  BENCH_THINK=low BENCH_THINK_MAX_CHARS=8192

echo "==== writing COMPARISON $(date) ===="
"$PY" -u - <<'PY'
import json
from pathlib import Path
out = Path("results/universal_matrix")
rows = []
p = out / "scores.jsonl"
if p.exists():
    for line in p.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
# dedupe by variant keeping last
by = {}
for r in rows:
    by[r["variant"]] = r
rows = list(by.values())
rows.sort(key=lambda r: (-r["universal_min"], -r["universal_mean"]))
lines = [
    "# Qwen3.6 universal sticky-config probe",
    "",
    "Same knobs on repohard/arch/pyhard subsets. Higher `universal_min` = less likely to nuke one task class.",
    "",
    "| rank | variant | mean% | min% | repo | arch | pyhard |",
    "|---:|---|---:|---:|---|---|---|",
]
for i, r in enumerate(rows, 1):
    lines.append(
        f"| {i} | `{r['variant']}` | {r['universal_mean']} | {r['universal_min']} | {r['repo']} ({r['repo_pct']}%) | {r['arch']} ({r['arch_pct']}%) | {r['pyhard']} ({r['py_pct']}%) |"
    )
lines += ["", f"n_variants={len(rows)}", ""]
(out / "COMPARISON.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print("\n".join(lines))
PY

echo "==== universal matrix ALL DONE $(date) ===="
