#!/bin/zsh
# Wait for the current resume bench to finish, then rerun thinking-heavy models
# with a larger num_predict budget.
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"

LOG="$HOME/.ollama/bench/results/pyhard_hibudget_wrapper.log"
exec >>"$LOG" 2>&1
echo "==== hibudget waiter start $(date) ===="

RESUME_LOG="$HOME/.ollama/bench/results/pyhard_resume_wrapper.log"
# Wait until resume script finishes (or benches go idle after north-mini results appear)
for i in $(seq 1 720); do  # up to ~6h at 30s
  if grep -q '^==== resume done' "$RESUME_LOG" 2>/dev/null; then
    echo "resume done detected $(date)"
    break
  fi
  # Fallback: both target latest files exist and no hard_bench_py running
  if [[ -f "$HOME/.ollama/bench/results/qwen3.6_35b-a3b-coding-bf16_pyhard_pyhard_latest.json" \
     && -f "$HOME/.ollama/bench/results/north-mini-code-1.0_bf16_pyhard_pyhard_latest.json" ]] \
     && ! pgrep -f 'hard_bench_py.py' >/dev/null 2>&1; then
    echo "latest results present and bench idle $(date)"
    break
  fi
  if (( i % 10 == 0 )); then
    echo "still waiting ($i) $(date)"
    pgrep -lf 'hard_bench_py' | head -3 || echo '(no bench proc)'
  fi
  sleep 30
done

# Extra settle so we don't race the resume compare step
sleep 5
if pgrep -f 'hard_bench_py.py' >/dev/null 2>&1; then
  echo "bench still running — waiting more..."
  while pgrep -f 'hard_bench_py.py' >/dev/null 2>&1; do sleep 30; done
fi

echo "==== hibudget bench start $(date) ===="
export BENCH_NUM_PREDICT=49152
export BENCH_NUM_CTX=65536
echo "BENCH_NUM_PREDICT=$BENCH_NUM_PREDICT BENCH_NUM_CTX=$BENCH_NUM_CTX"

# Distinct tags so we keep the 16k baseline results
for model in 'qwen3.6:35b-a3b-coding-bf16' 'north-mini-code-1.0:bf16'; do
  tag="$(echo "$model" | sed 's/[^a-zA-Z0-9._-]/_/g')_pyhard_p49k"
  echo "---- $model tag=$tag ----"
  BENCH_MODEL="$model" BENCH_TAG="$tag" \
    "$(uv python find 3.14)" "$HOME/.ollama/bench/hard_bench_py.py"
done

# Append compare note
/Users/deirh/.local/bin/python3.14 <<'PY'
import json
from pathlib import Path
out = Path.home() / ".ollama" / "bench" / "results"
lines = ["", "# Hi-budget rerun (num_predict=49152)", ""]
for tag, name in [
    ("qwen3.6_35b-a3b-coding-bf16_pyhard_p49k", "Qwen3.6 Coding BF16 @49k"),
    ("north-mini-code-1.0_bf16_pyhard_p49k", "North Mini Code BF16 @49k"),
]:
    p = out / f"{tag}_pyhard_latest.json"
    if not p.exists():
        lines.append(f"- {name}: missing")
        continue
    rows = json.loads(p.read_text())
    score = sum(r["score"] for r in rows)
    mx = sum(r["max_score"] for r in rows)
    length_hits = sum(1 for r in rows if r.get("done_reason") == "length")
    lines.append(f"- **{name}**: {score}/{mx}, pass {sum(1 for r in rows if r['ok'])}/{len(rows)}, length_hits={length_hits}")
    for r in rows:
        lines.append(f"  - {r['task']}: {'PASS' if r['ok'] else 'FAIL'} {r['score']}/{r['max_score']} reason={r.get('done_reason')}")
path = out / "compare_pyhard_hibudget.md"
path.write_text("\n".join(lines) + "\n")
print("\n".join(lines))
print("Wrote", path)
# also append to main compare
main = out / "compare_pyhard_64k.md"
if main.exists():
    main.write_text(main.read_text() + "\n" + "\n".join(lines) + "\n")
PY

echo "==== hibudget done $(date) ===="
