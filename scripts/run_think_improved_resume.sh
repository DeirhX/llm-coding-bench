#!/bin/zsh
# Resume think-improved after Ollama unload deadlock mid-task.
# Merges any completed tasks from the interrupted timestamped run, then finishes
# remaining pyhard tasks and continues the original queue (3.6 + arch think).
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="$(command -v python3.14 || true)"
[[ -n "$PY" ]] || PY="$(uv python find 3.14)"

export BENCH_OUT="${BENCH_OUT:-$ROOT/results}"
mkdir -p "$BENCH_OUT" "$BENCH_OUT/archbench"
LOG="$BENCH_OUT/think_improved.log"
: >>"$LOG"
exec >>"$LOG" 2>&1

export BENCH_THINK=medium
export BENCH_NUM_CTX=65536
export BENCH_NUM_PREDICT=49152
export BENCH_TASK_TIMEOUT_S="${BENCH_TASK_TIMEOUT_S:-600}"
export BENCH_CURSOR_TIMEOUT="${BENCH_CURSOR_TIMEOUT:-$BENCH_TASK_TIMEOUT_S}"

echo "==== think-improved RESUME $(date) ===="

# Merge completed tasks from interrupted timestamped JSON into *_latest.json
"$PY" - <<'PY'
import json
from pathlib import Path
out = Path("results")
partial = sorted(out.glob("qwen3.5_35b-a3b-coding-bf16_pyhard_pyhard_2026*.json"))[-1]
latest = out / "qwen3.5_35b-a3b-coding-bf16_pyhard_pyhard_latest.json"
order = [
    "regex_match","lru_cache","alien_order","eval_expr","fix_vm",
    "sat_solve","json_patch","unify","mini_sql",
]
by = {r["task"]: r for r in json.loads(latest.read_text())}
for r in json.loads(partial.read_text()):
    by[r["task"]] = r
    print(f"merged from partial: {r['task']} {r['score']}/{r['max_score']} think={r.get('think')}")
merged = [by[t] for t in order if t in by]
latest.write_text(json.dumps(merged, indent=2) + "\n")
done = {r["task"] for r in json.loads(partial.read_text())}
# Prefer redoing the hung task even if somehow present
hung = "lru_cache"
todo = [t for t in order if t not in done or t == hung]
# if hung was never written, it's already in todo
print("TODO=" + ",".join(todo))
Path("/tmp/think_resume_todo.txt").write_text(",".join(todo))
PY

TODO="$(cat /tmp/think_resume_todo.txt)"
echo "resuming qwen3.5 pyhard tasks: $TODO"

BENCH_MODEL='qwen3.5:35b-a3b-coding-bf16' \
  BENCH_TAG='qwen3.5_35b-a3b-coding-bf16_pyhard' \
  BENCH_TASKS="$TODO" \
  BENCH_MERGE_LATEST=1 \
  "$PY" -u "$ROOT/run.py" run pyhard
echo "---- pyhard done qwen3.5 resume $(date) ----"

echo "---- pyhard qwen3.6 $(date) ----"
BENCH_MODEL='qwen3.6:35b-a3b-coding-bf16' \
  BENCH_TAG='qwen3.6_35b-a3b-coding-bf16_pyhard' \
  "$PY" -u "$ROOT/run.py" run pyhard
echo "---- pyhard done qwen3.6 $(date) ----"

echo "---- arch qwen3.5 think $(date) ----"
BENCH_MODEL='qwen3.5:35b-a3b-coding-bf16' \
  BENCH_TAG='qwen3.5_35b-a3b-coding-bf16_arch_think' \
  BENCH_NUM_PREDICT=24576 \
  "$PY" -u "$ROOT/run.py" run arch
echo "---- arch done qwen3.5 $(date) ----"

echo "---- arch qwen3.6 think $(date) ----"
BENCH_MODEL='qwen3.6:35b-a3b-coding-bf16' \
  BENCH_TAG='qwen3.6_35b-a3b-coding-bf16_arch_think' \
  BENCH_NUM_PREDICT=24576 \
  "$PY" -u "$ROOT/run.py" run arch \
  || echo "WARN arch qwen3.6 failed/timeout $(date)"
echo "---- arch done qwen3.6 $(date) ----"

echo "==== rescore $(date) ===="
BENCH_SELFTEST=1 "$PY" -m benches.pyhard.rescore || true
BENCH_SELFTEST=1 "$PY" -m benches.arch.rescore || true
echo "==== think-improved ALL DONE $(date) ===="
