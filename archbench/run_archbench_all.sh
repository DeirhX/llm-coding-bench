#!/bin/zsh
# Wait until pyhard hibudget finishes, then run archbench (+ claim probe on ties)
# on all local coding-relevant models.
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"

ROOT="$HOME/.ollama/bench/archbench"
PY="$(command -v python3.14 || true)"
if [[ -z "$PY" && -x "$HOME/.local/bin/python3.14" ]]; then
  PY="$HOME/.local/bin/python3.14"
fi
if [[ -z "$PY" ]]; then
  PY="$(uv python find 3.14)"
fi

LOG="$HOME/.ollama/bench/results/archbench/archbench_all_wrapper.log"
mkdir -p "$HOME/.ollama/bench/results/archbench"
exec >>"$LOG" 2>&1

echo "==== archbench-all waiter start $(date) ===="

# Wait for hibudget to finish (marker) and no hard_bench_py
HIBU_LOG="$HOME/.ollama/bench/results/pyhard_hibudget_wrapper.log"
for i in $(seq 1 1440); do  # up to ~12h @ 30s
  hibudone=0
  if grep -q '^==== hibudget done' "$HIBU_LOG" 2>/dev/null; then
    hibudone=1
  fi
  # Also accept: hibudget not running AND north 49k results exist
  if [[ "$hibudone" -eq 0 ]] \
     && ! pgrep -f 'run_pyhard_hibudget.sh' >/dev/null 2>&1 \
     && ls "$HOME/.ollama/bench/results/"north-mini-code-1.0_bf16_pyhard_p49k*.json >/dev/null 2>&1; then
    hibudone=1
  fi
  if [[ "$hibudone" -eq 1 ]] && ! pgrep -f 'hard_bench_py.py' >/dev/null 2>&1; then
    echo "hibudget complete / idle $(date)"
    break
  fi
  if (( i % 10 == 0 )); then
    echo "still waiting ($i) $(date) hibudone=$hibudone"
    pgrep -lf 'hard_bench_py|hibudget' | head -5 || echo '(no matching procs)'
  fi
  sleep 30
done

# Extra settle for model unload
sleep 10
echo "==== archbench-all start $(date) ===="

cd "$ROOT"
BENCH_SELFTEST=1 "$PY" "$ROOT/arch_bench.py"
BENCH_SELFTEST=1 "$PY" "$ROOT/claim_bench.py"

# All installed models (skip nothing — user asked for all)
MODELS=(
  "qwen3-coder-next:q8_0"
  "qwen3-coder:30b-a3b-fp16"
  "gpt-oss:120b"
  "qwen2.5-coder:32b-instruct-q8_0"
  "devstral:24b-small-2505-fp16"
  "qwen3.5:35b-a3b-coding-bf16"
  "qwen3.6:35b-a3b-coding-bf16"
  "north-mini-code-1.0:bf16"
  "llama3.3:70b-instruct-q8_0"
  "deepseek-r1:70b-llama-distill-q8_0"
)

for model in "${MODELS[@]}"; do
  tag="$(echo "$model" | sed 's/[^a-zA-Z0-9._-]/_/g')_arch"
  latest="$HOME/.ollama/bench/results/archbench/${tag}_latest.json"
  if [[ -f "$latest" ]]; then
    # resume: skip completed full runs only if not dominated by transport errors
    stats="$("$PY" -c "
import json
d=json.load(open('$latest'))
n=len(d) if isinstance(d,list) else 0
errs=sum(1 for r in d if 'HTTPError' in str(r.get('grade_detail','')) or 'ERROR:' in str(r.get('grade_detail','')))
print(f'{n} {errs}')
")"
    n="${stats%% *}"
    errs="${stats##* }"
    if [[ "$n" -ge 9 && "$errs" -eq 0 ]]; then
      echo "skip completed $model ($n tasks, 0 transport errors)"
      continue
    fi
    if [[ "$n" -ge 9 && "$errs" -gt 0 ]]; then
      echo "re-run $model: $errs/$n tasks had transport errors"
      rm -f "$latest"
    fi
  fi
  echo "---- archbench $model tag=$tag $(date) ----"
  if ! BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" "$ROOT/arch_bench.py"; then
    echo "WARN: archbench failed for $model (continuing)"
  fi
done

# Second pass: re-run any arch results polluted by Ollama HTTP 500s / transport errors
echo "==== archbench error re-pass $(date) ===="
SKIP_MODELS=(
  "deepseek-r1:70b-llama-distill-q8_0"
)
for model in "${MODELS[@]}"; do
  skip=0
  for s in "${SKIP_MODELS[@]}"; do
    [[ "$model" == "$s" ]] && skip=1 && break
  done
  if [[ "$skip" -eq 1 ]]; then
    echo "skip re-pass $model (user skip list)"
    continue
  fi
  tag="$(echo "$model" | sed 's/[^a-zA-Z0-9._-]/_/g')_arch"
  latest="$HOME/.ollama/bench/results/archbench/${tag}_latest.json"
  [[ -f "$latest" ]] || continue
  errs="$("$PY" -c "
import json
d=json.load(open('$latest'))
if isinstance(d, dict) and d.get('skipped'):
    print(-1)
elif isinstance(d, list):
    print(sum(1 for r in d if 'HTTPError' in str(r.get('grade_detail','')) or str(r.get('grade_detail','')).startswith('ERROR:')))
else:
    print(0)
")"
  if [[ "$errs" -eq -1 ]]; then
    echo "skip re-pass $model (skipped stub)"
    continue
  fi
  if [[ "$errs" -gt 0 ]]; then
    echo "re-pass $model ($errs error tasks)"
    rm -f "$latest"
    BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" "$ROOT/arch_bench.py" || echo "WARN: re-pass failed $model"
  fi
done

echo "==== analyze + maybe claim probe $(date) ===="
"$PY" "$ROOT/analyze_arch_results.py"

echo "==== archbench-all done $(date) ===="
