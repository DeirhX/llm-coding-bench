#!/bin/zsh
# Fetch the two variants that could plausibly beat 7.9 tok/s on the 31B.
#
# The 31B is not slow for want of tuning. At bf16 it reads 62GB of weights per token
# and produces 7.9 tok/s, which is 490 GB/s against this machine's 614 GB/s ceiling --
# 80% of theoretical peak, i.e. the memory bus is saturated and no runtime flag will
# help. Only two things can: read fewer bytes per token, or emit more than one token
# per read.
#
#   31b-it-qat            quantization-aware trained, so the 4-bit rounding was part of
#                         training rather than applied afterwards. ~19GB of weights
#                         predicts ~26 tok/s at the same bus efficiency. The reason to
#                         prefer this over plain q4_K_M is that quantization damage
#                         lands hardest on careful-discrimination behaviour, which is
#                         exactly what the trap suite measures and what we spent this
#                         whole exercise establishing.
#
#   31b-coding-mtp-bf16   multi-token prediction: proposes several tokens ahead and
#                         verifies them in one forward pass, breaking the one-token-
#                         per-62GB-read constraint without touching precision. Also
#                         coding-specialised, so any quality delta is confounded with
#                         the speed delta and both need measuring separately.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG="$ROOT/results/pull_speed_variants.log"
mkdir -p results modelfiles
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== pulling speed variants $(date) ===="

have_model() {
  ollama list 2>/dev/null | awk -v m="$1" '$1 == m { found = 1 } END { exit found ? 0 : 1 }'
}

pull_with_retry() {
  local model="$1"
  if have_model "$model"; then
    echo "---- SKIP $model: already present ----"
    return 0
  fi
  for attempt in 1 2 3 4 5; do
    echo "---- PULL $model attempt $attempt $(date) ----"
    if ollama pull "$model"; then
      echo "---- PULLED $model $(date) ----"
      return 0
    fi
    echo "---- retry $model in 60s $(date) ----" >&2
    sleep 60
  done
  echo "---- FAILED $model after 5 attempts $(date) ----" >&2
  return 1
}

pull_with_retry 'gemma4:31b-it-qat'
pull_with_retry 'gemma4:31b-coding-mtp-bf16'

# Register tuned variants carrying the same sampler, bound and stop markers as the
# bf16 model already in use, so the comparison isolates weights and decoding strategy
# rather than accidentally also changing temperature.
build_tuned() {
  local base="$1" name="$2" file="$3"
  if ! have_model "$base"; then
    echo "---- SKIP build $name: base $base missing ----" >&2
    return 0
  fi
  echo "---- BUILD $name from $base $(date) ----"
  ollama create "$name" -f "$file" || echo "---- FAILED build $name $(date) ----" >&2
}

build_tuned 'gemma4:31b-it-qat'          'gemma4-coding:31b-qat' "$ROOT/modelfiles/gemma4-31b-coding-qat.Modelfile"
build_tuned 'gemma4:31b-coding-mtp-bf16' 'gemma4-coding:31b-mtp' "$ROOT/modelfiles/gemma4-31b-coding-mtp.Modelfile"

echo
ollama list | rg 'gemma4' || true
echo "==== ALL DONE pull speed variants $(date)"
