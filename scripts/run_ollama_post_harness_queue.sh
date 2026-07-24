#!/bin/zsh
# After universal matrix: bring local Ollama models onto post-harness scoring.
# - claim: must re-run (15→20 claims, max≈23)
# - arch/pyhard/repohard: re-run if missing full post-harness cell; else rescore offline
#
# Sticky knobs (pending matrix winner override via BENCH_THINK):
#   think-off, ctx 64k, predict 24k — the universal default until matrix says otherwise.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"
LOG="$ROOT/results/ollama_post_harness_queue.log"
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== ollama post-harness queue start $(date) ===="

# If matrix left a winner in COMPARISON, prefer its think setting; else off.
WINNER_THINK=0
if [[ -f "$ROOT/results/universal_matrix/COMPARISON.md" ]]; then
  # First ranked variant name
  top="$("$PY" - <<'PY'
from pathlib import Path
import re
text = Path("results/universal_matrix/COMPARISON.md").read_text()
for line in text.splitlines():
    m = re.search(r"\| 1 \| `([^`]+)`", line)
    if m:
        print(m.group(1))
        break
PY
)"
  echo "matrix top variant: ${top:-none}"
  case "$top" in
    off|off_p16k) WINNER_THINK=0 ;;
    low|low_fin15|low_c16k|low_c8k) WINNER_THINK=low ;;
    med|med_fin15|med_c16k|med_c24k) WINNER_THINK=medium ;;
    high) WINNER_THINK=high ;;
    think_true) WINNER_THINK=1 ;;
    *) WINNER_THINK=0 ;;
  esac
fi

export BENCH_PROVIDER=ollama
export BENCH_THINK="${BENCH_THINK:-$WINNER_THINK}"
export BENCH_THINK_MAX_CHARS="${BENCH_THINK_MAX_CHARS:-0}"
export BENCH_THINK_PROMOTE=1
export BENCH_THINK_LOOP=1
unset BENCH_THINK_ROUNDS
export BENCH_NUM_CTX=65536
export BENCH_NUM_PREDICT=24576
export BENCH_TEMPERATURE=0.1
export BENCH_TASK_TIMEOUT_S=1200
export BENCH_OUT="$ROOT/results"
export BENCH_MERGE_LATEST=0
unset BENCH_TASKS

echo "queue THINK=$BENCH_THINK MAX_CHARS=$BENCH_THINK_MAX_CHARS PREDICT=$BENCH_NUM_PREDICT"

# Offline rescore first (cheap).
echo "---- rescore pass $(date) ----"
BENCH_SELFTEST=1 "$PY" -m benches.pyhard.rescore || true
BENCH_SELFTEST=1 "$PY" -m benches.arch.rescore || true
BENCH_SELFTEST=1 "$PY" -m benches.repohard.rescore || true

# Models still on pre-harness claim (max_score=18) or missing claim entirely.
# Format: ollama_tag
MODELS=(
  'qwen3.6:35b-a3b-coding-bf16'
  'qwen3.5:35b-a3b-coding-bf16'
  'qwen3-coder-next:q8_0'
  'qwen3-coder:30b-a3b-fp16'
  'qwen2.5-coder:32b-instruct-q8_0'
  'gpt-oss:120b'
  'north-mini-code-1.0:bf16'
  'devstral:24b-small-2505-fp16'
  'llama3.3:70b-instruct-q8_0'
  'deepseek-r1:70b-llama-distill-q8_0'
)

safe_name() {
  echo "$1" | sed 's/[^a-zA-Z0-9._-]/_/g'
}

needs_claim_rerun() {
  local safe="$1"
  "$PY" - <<PY
import json
from pathlib import Path
cands = list(Path("results/archbench").glob(f"${safe}_claim*latest.json"))
cands = [p for p in cands if "think_" not in p.name and "univ" not in p.name]
if not cands:
    print("yes")
    raise SystemExit(0)
# prefer plain latest
plain = Path("results/archbench") / f"${safe}_claim_latest.json"
p = plain if plain.is_file() else cands[0]
o = json.loads(p.read_text())
mx = int(o.get("max_score") or 0)
corr = int(o.get("correct") or 0)
n = len(o.get("per_claim") or [])
# Post-harness: 20 claims, max_score typically 23
if mx >= 20 and n >= 20:
    print("no")
else:
    print("yes")
PY
}

run_bench() {
  local model="$1" bench="$2" tag="$3"
  echo "---- $bench model=$model tag=$tag $(date) ----"
  export BENCH_MODEL="$model"
  export BENCH_TAG="$tag"
  git -C "$ROOT" checkout -- benches/repohard/fixture/ledgerkit/ 2>/dev/null || true
  "$PY" -u "$ROOT/run.py" run "$bench" || echo "WARN $bench rc=$?"
  "$PY" -u "$ROOT/run.py" report "$bench" --no-color || true
}

for model in "${MODELS[@]}"; do
  safe="$(safe_name "$model")"
  echo "==== model $model $(date) ===="
  # Skip if model not pulled
  if ! ollama show "$model" >/dev/null 2>&1; then
    echo "SKIP not installed: $model"
    continue
  fi

  if [[ "$(needs_claim_rerun "$safe")" == "yes" ]]; then
    run_bench "$model" claim "${safe}_claim"
  else
    echo "claim OK post-harness for $safe"
  fi

  # Arch: re-run if missing full 9 tasks; else leave rescored
  "$PY" - <<PY || run_bench "$model" arch "${safe}_arch"
import json
from pathlib import Path
safe = "$safe"
for p in [
    Path(f"results/archbench/{safe}_arch_rescored_latest.json"),
    Path(f"results/archbench/{safe}_arch_latest.json"),
]:
    if p.is_file():
        rows = json.loads(p.read_text())
        if isinstance(rows, list) and len(rows) >= 9:
            print(f"arch OK {p.name}")
            raise SystemExit(0)
print("arch missing")
raise SystemExit(1)
PY

  # Pyhard: re-run if missing 9 tasks
  "$PY" - <<PY || run_bench "$model" pyhard "${safe}_pyhard"
import json
from pathlib import Path
safe = "$safe"
cands = list(Path("results").glob(f"{safe}_pyhard*latest.json"))
cands = [p for p in cands if "rescored" not in p.name and "think_" not in p.name and "univ" not in p.name and "p49k" not in p.name and "nothink" not in p.name and "rerun" not in p.name]
# also accept nothink as post if complete
alts = list(Path("results").glob(f"{safe}*pyhard*latest.json"))
alts = [p for p in alts if "rescored" not in p.name and "univ" not in p.name]
for p in cands + alts:
    rows = json.loads(p.read_text())
    if isinstance(rows, list) and len(rows) >= 9:
        print(f"pyhard OK {p.name}")
        raise SystemExit(0)
print("pyhard missing")
raise SystemExit(1)
PY

  # Repohard: re-run if missing 8 tasks
  "$PY" - <<PY || run_bench "$model" repohard "${safe}_repohard"
import json
from pathlib import Path
safe = "$safe"
for p in [
    Path(f"results/repohard/{safe}_repohard_latest.json"),
    Path(f"results/repohard/{safe}_repohard_rescored_latest.json"),
]:
    if p.is_file():
        rows = json.loads(p.read_text())
        if isinstance(rows, list) and len(rows) >= 8:
            print(f"repohard OK {p.name}")
            raise SystemExit(0)
print("repohard missing")
raise SystemExit(1)
PY
done

echo "==== final rescore $(date) ===="
BENCH_SELFTEST=1 "$PY" -m benches.pyhard.rescore || true
BENCH_SELFTEST=1 "$PY" -m benches.arch.rescore || true
BENCH_SELFTEST=1 "$PY" -m benches.repohard.rescore || true
"$PY" -u "$ROOT/run.py" report || true
echo "==== ollama post-harness queue ALL DONE $(date) ===="
