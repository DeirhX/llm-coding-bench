#!/bin/zsh
# Does injecting the depth contract cost anything on the benches that already measure quality?
#
# The gate is worth having only if it makes answers better without making them worse elsewhere, and
# "elsewhere" is measurable here: arch scores evidence points, claim scores accuracy, audittrap
# counts how often the model patches a bug that does not exist. Each runs twice, identical but for
# the contract, under BENCH_REALISM=1 so the thinking channel behaves as it does in a real client.
#
# The model is not a parameter by accident. One 31B runner fits in this machine, and naming a
# variant other than the resident one evicts it and costs both arms a cold load plus a full
# re-prefill. So the default is read from what is actually loaded, and overriding it is a
# deliberate act.
#
# audittrap's contract arm keeps system_local.md and appends the contract, because every 20/20 trap
# result on record was measured with those 63 words present; replacing them would confound "the
# contract cost trap discipline" with "removing the prompt cost trap discipline". arch and claim
# have no baseline prompt, so their contract arm is the contract alone.
set -uo pipefail
ROOT="${0:A:h:h}"
cd "$ROOT"

# The repository venv, not whatever python3 is on PATH: the fix tasks shell out to pytest, and the
# interpreters on PATH here do not have it. A run under the wrong one scores every fix task 0 and
# looks like a model result. run_private_pytest now refuses that, and this picks the right one.
PY="${PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14 || command -v python3)"
OLLAMA_URL="${OLLAMA_HOST_URL:-http://127.0.0.1:11434}"
ADAPTER="${DEPTH_ADAPTER:-review}"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$ROOT/results/depth_gate_$STAMP"
mkdir -p "$OUT"

MODEL="${BENCH_MODEL:-$(curl -sf --max-time 5 "$OLLAMA_URL/api/ps" \
  | "$PY" -c 'import json,sys; m=(json.load(sys.stdin).get("models") or [{}])[0]; print(m.get("name",""))' 2>/dev/null)}"
if [[ -z "$MODEL" ]]; then
  echo "error: no model resident and BENCH_MODEL unset. Load one first, or name it." >&2
  exit 1
fi
echo "model:   $MODEL (resident; not switching variants)"
echo "adapter: $ADAPTER"
echo "out:     $OUT"

# The contract exactly as a session would receive it, so the bench measures the deployed text.
CONTRACT="$OUT/contract.md"
"$PY" "$ROOT/scripts/cc_ledger.py" "$ADAPTER" > "$CONTRACT" || exit 1
AUDIT_CONTRACT="$OUT/contract_audittrap.md"
cat "$ROOT/benches/audittrap/system_local.md" > "$AUDIT_CONTRACT" 2>/dev/null || true
printf '\n' >> "$AUDIT_CONTRACT"
cat "$CONTRACT" >> "$AUDIT_CONTRACT"

export BENCH_OUT="$OUT"
export BENCH_REALISM=1

run_arm() {
  local bench="$1" arm="$2" prompt_file="$3"
  local tag="depth_${bench}_${arm}"
  echo "---- $bench / $arm ----"
  if [[ -n "$prompt_file" ]]; then
    BENCH_SYSTEM_PROMPT_FILE="$prompt_file" BENCH_SYSTEM_PROMPT=1 \
      BENCH_MODEL="$MODEL" BENCH_TAG="$tag" "$PY" "$ROOT/run.py" run "$bench" 2>&1 \
      | tee "$OUT/$tag.log" | tail -3
  else
    BENCH_SYSTEM_PROMPT=0 \
      BENCH_MODEL="$MODEL" BENCH_TAG="$tag" "$PY" "$ROOT/run.py" run "$bench" 2>&1 \
      | tee "$OUT/$tag.log" | tail -3
  fi
}

for bench in arch claim; do
  run_arm "$bench" baseline ""
  run_arm "$bench" contract "$CONTRACT"
done
run_arm audittrap baseline "$ROOT/benches/audittrap/system_local.md"
run_arm audittrap contract "$AUDIT_CONTRACT"

echo
"$PY" "$ROOT/scripts/measure_depth_gate.py" "$OUT"
