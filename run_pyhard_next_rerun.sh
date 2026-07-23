#!/bin/zsh
# Wait for archbench-all to finish, then re-run pyhard coding bench for Next
# (and 30B as the efficiency twin for a clean head-to-head).
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"

LOG="$HOME/.ollama/bench/results/pyhard_next_rerun_wrapper.log"
mkdir -p "$HOME/.ollama/bench/results"
exec >>"$LOG" 2>&1

echo "==== pyhard next-rerun waiter start $(date) ===="
ARCH_LOG="$HOME/.ollama/bench/results/archbench/archbench_all_wrapper.log"

for i in $(seq 1 1440); do
  if grep -q '^==== archbench-all done' "$ARCH_LOG" 2>/dev/null \
     && ! pgrep -f 'arch_bench.py' >/dev/null 2>&1 \
     && ! pgrep -f 'run_archbench_all.sh' >/dev/null 2>&1 \
     && ! pgrep -f 'claim_bench.py' >/dev/null 2>&1; then
    echo "archbench idle $(date)"
    break
  fi
  if (( i % 10 == 0 )); then
    echo "still waiting ($i) $(date)"
    pgrep -lf 'arch_bench|run_archbench|claim_bench' | head -5 || echo '(no arch procs)'
  fi
  sleep 30
done

# settle model unload
sleep 10
if pgrep -f 'hard_bench_py.py' >/dev/null 2>&1; then
  echo "another pyhard still running — waiting..."
  while pgrep -f 'hard_bench_py.py' >/dev/null 2>&1; do sleep 30; done
fi

echo "==== pyhard next-rerun start $(date) ===="
export BENCH_NUM_CTX=65536
export BENCH_NUM_PREDICT=16384
PY="$(command -v python3.14 || true)"
[[ -n "$PY" ]] || PY="$(uv python find 3.14)"

for model in 'qwen3-coder-next:q8_0' 'qwen3-coder:30b-a3b-fp16'; do
  tag="$(echo "$model" | sed 's/[^a-zA-Z0-9._-]/_/g')_pyhard_rerun"
  echo "---- $model tag=$tag ----"
  BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" "$HOME/.ollama/bench/hard_bench_py.py" \
    || echo "WARN: failed $model"
done

"$PY" <<'PY'
import json
from pathlib import Path
out = Path.home() / ".ollama" / "bench" / "results"
lines = ["", "# Pyhard re-run after archbench (num_predict=16384)", ""]
for tag, name in [
    ("qwen3-coder-next_q8_0_pyhard_rerun", "Next Q8 re-run"),
    ("qwen3-coder_30b-a3b-fp16_pyhard_rerun", "30B-A3B FP16 re-run"),
]:
    # harness may append _pyhard again depending on TAG usage
    cands = sorted(out.glob(f"{tag}*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    latest = None
    for p in cands:
        if p.name.endswith("_latest.json") or "_20" in p.name:
            try:
                d = json.loads(p.read_text())
            except Exception:
                continue
            if isinstance(d, list) and d:
                latest = p
                rows = d
                break
    if not latest:
        # also try tag_pyhard_latest double suffix quirk
        for p in sorted(out.glob(f"{tag}*latest.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            d = json.loads(p.read_text())
            if isinstance(d, list) and d:
                latest, rows = p, d
                break
    if not latest:
        lines.append(f"- {name}: missing")
        continue
    score = sum(r["score"] for r in rows)
    mx = sum(r["max_score"] for r in rows)
    wall = sum(r.get("wall_s") or 0 for r in rows)
    lines.append(
        f"- **{name}**: {score}/{mx}, pass {sum(1 for r in rows if r['ok'])}/{len(rows)}, "
        f"wall={wall:.1f}s  ({latest.name})"
    )
    for r in rows:
        lines.append(
            f"  - {r['task']}: {'PASS' if r['ok'] else 'FAIL'} {r['score']}/{r['max_score']} "
            f"reason={r.get('done_reason')} wall={r.get('wall_s')}"
        )
path = out / "compare_pyhard_rerun.md"
path.write_text("\n".join(lines) + "\n")
print("\n".join(lines))
print("Wrote", path)
main = out / "compare_pyhard_64k.md"
if main.exists():
    main.write_text(main.read_text() + "\n" + "\n".join(lines) + "\n")
PY

echo "==== pyhard next-rerun done $(date) ===="
