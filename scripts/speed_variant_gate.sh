#!/bin/zsh
# Can the 31B be made usable at interactive speed without becoming a worse model?
#
# At bf16 it reads 62GB of weights per generated token and manages 7.9 tok/s, which is
# 490 GB/s against this machine's 614 GB/s ceiling. 80% of peak means the memory bus is
# saturated: there is no runtime flag, thread count or attention trick left to find.
# Only two things can help, and they are tested here one at a time.
#
#   31b-it-qat            ~19GB of weights instead of 62GB. Predicts ~26 tok/s at the
#                         same bus efficiency. Quantization-aware training rather than
#                         post-training rounding, because the behaviour at risk is
#                         careful discrimination -- which is precisely what the trap
#                         suite measures and what took this whole exercise to establish.
#
#   31b-coding-mtp-bf16   full precision, but predicts several tokens per forward pass,
#                         so it breaks the one-token-per-62GB-read constraint instead of
#                         shrinking the read. Also coding-specialised, so a score change
#                         has two candidate causes and only the speed reading is clean.
#
# EVERY SCORE IN THIS REPO WAS MEASURED AT BF16. A 3x speedup that quietly returns the
# 31B to believing every bug report it is handed is not a bargain, and 4-bit damage
# shows up in exactly that kind of judgement long before it shows up in perplexity. So
# each variant faces the full gate: the trap suite under the deployable 63-word prompt,
# repohard with no harness rescues, and a throughput probe -- identical conditions to
# the bf16 runs they are being compared against.
#
# Ordered small-model-first deliberately. Pulling 62GB while measuring decode speed
# would put disk contention inside the very number under measurement.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"

LOG="$ROOT/results/speed_variant_gate.log"
mkdir -p results modelfiles
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== speed variant gate $(date) ===="

have_model() {
  ollama list 2>/dev/null | awk -v m="$1" '$1 == m { found = 1 } END { exit found ? 0 : 1 }'
}

pull_with_retry() {
  local model="$1"
  if have_model "$model"; then
    echo "---- SKIP pull $model: already present ----"
    return 0
  fi
  # Progress goes to its own file: it is carriage-return animation that would otherwise
  # bury the log, and the only reliable success test is whether the model then exists,
  # not the exit status of a pipeline containing rg.
  local prog="$ROOT/results/pull_progress_${model//[:\/]/_}.txt"
  for attempt in 1 2 3 4 5; do
    echo "---- PULL $model attempt $attempt $(date) ----"
    ollama pull "$model" >"$prog" 2>&1
    tr '\r' '\n' <"$prog" | rg -v '^\s*$' | tail -2
    if have_model "$model"; then
      echo "---- PULLED $model $(date) ----"
      return 0
    fi
    echo "---- retry $model in 60s $(date) ----" >&2
    sleep 60
  done
  echo "---- FAILED pull $model $(date) ----" >&2
  return 1
}

build_tuned() {
  local base="$1" name="$2" file="$3"
  have_model "$base" || { echo "---- SKIP build $name: base missing ----" >&2; return 1; }
  echo "---- BUILD $name from $base $(date) ----"
  ollama create "$name" -f "$file" >/dev/null 2>&1 \
    && echo "---- BUILT $name ----" \
    || { echo "---- FAILED build $name ----" >&2; return 1; }
}

speed() {
  echo
  echo "---- START speed probe: $* $(date) ----"
  "$PY" -u scripts/decode_speed_probe.py "$@" || echo "---- FAILED speed probe rc=$? ----" >&2
  echo "---- DONE speed probe $(date) ----"
}

# Trap discipline under the prompt that is actually deployed. skeptic_min.md is 63
# generic words with no bench vocabulary in it; it took the bf16 31B from 0/20 to 20/20
# on the traps at zero cost to its 38/40 on real fixes. Whether a quantized copy still
# has the judgement to use it is the whole question.
audit_arm() {
  local model="$1" tag="$2"
  have_model "$model" || { echo "---- SKIP $tag: $model missing ----" >&2; return 0; }
  echo
  echo "---- START $tag model=$model $(date) ----"
  (
    export BENCH_PROVIDER=ollama
    export BENCH_REALISM=1
    export BENCH_TEMPERATURE=auto
    export BENCH_THINK=0
    export BENCH_MAX_ROUNDS=40 BENCH_MAX_TOOL_CALLS=40
    export BENCH_TASK_TIMEOUT_S=1200 BENCH_NUM_CTX=65536
    export BENCH_KEEP_ALIVE=24h
    unset BENCH_NUM_PREDICT BENCH_FINALIZE_AFTER BENCH_THINK_MAX_CHARS || true
    unset BENCH_THINK_LOOP BENCH_THINK_PROMOTE BENCH_STOP_FABRICATION || true
    export BENCH_SYSTEM_PROMPT=1 BENCH_SYSTEM_PROMPT_FILE='prompts/skeptic_min.md'
    BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" -u -m benches.audittrap
  ) || echo "---- FAILED $tag rc=$? $(date) ----" >&2
  echo "---- DONE $tag $(date) ----"
  ollama stop "$model" 2>/dev/null || true
}

repo_arm() {
  local model="$1" tag="$2"
  have_model "$model" || { echo "---- SKIP $tag: $model missing ----" >&2; return 0; }
  echo
  echo "---- START $tag model=$model $(date) ----"
  (
    export BENCH_PROVIDER=ollama
    export BENCH_REALISM=1
    export BENCH_TEMPERATURE=auto
    export BENCH_THINK=0
    export BENCH_NUM_PREDICT=24576
    export BENCH_MAX_ROUNDS=40 BENCH_MAX_TOOL_CALLS=40
    export BENCH_TASK_TIMEOUT_S=1200 BENCH_NUM_CTX=65536
    export BENCH_KEEP_ALIVE=24h
    export BENCH_SYSTEM_PROMPT=0
    unset BENCH_SYSTEM_PROMPT_FILE BENCH_FINALIZE_AFTER || true
    BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" -u -m benches.repohard
  ) || echo "---- FAILED $tag rc=$? $(date) ----" >&2
  echo "---- DONE $tag $(date) ----"
  ollama stop "$model" 2>/dev/null || true
}

echo
echo "======== STAGE 0: baseline throughput on what is deployed today ========"
echo "Measured now rather than inferred from old wall-clock, so the variants are"
echo "compared against the same machine in the same thermal state."
speed 'gemma4-coding:31b' 'gemma4-coding:26b-a4b'

echo
echo "======== STAGE 1: QAT, the small one ========"
if pull_with_retry 'gemma4:31b-it-qat' \
   && build_tuned 'gemma4:31b-it-qat' 'gemma4-coding:31b-qat' "$ROOT/modelfiles/gemma4-31b-coding-qat.Modelfile"; then
  speed 'gemma4-coding:31b-qat'
  audit_arm 'gemma4-coding:31b-qat' 'gemma4-coding_31b-qat_audittrap_skeptic_min'
  repo_arm  'gemma4-coding:31b-qat' 'gemma4-coding_31b-qat_repohard_np24576'
else
  echo "---- STAGE 1 unavailable, continuing ----" >&2
fi

echo
echo "======== STAGE 2: MTP, the large one ========"
if pull_with_retry 'gemma4:31b-coding-mtp-bf16' \
   && build_tuned 'gemma4:31b-coding-mtp-bf16' 'gemma4-coding:31b-mtp' "$ROOT/modelfiles/gemma4-31b-coding-mtp.Modelfile"; then
  speed 'gemma4-coding:31b-mtp'
  audit_arm 'gemma4-coding:31b-mtp' 'gemma4-coding_31b-mtp_audittrap_skeptic_min'
  repo_arm  'gemma4-coding:31b-mtp' 'gemma4-coding_31b-mtp_repohard_np24576'
else
  echo "---- STAGE 2 unavailable, continuing ----" >&2
fi

"$PY" - <<'PY'
import json
import statistics
from pathlib import Path

PEAK_GBPS = 614.4
TRAPS = {"sat_assign_cleared", "sql_where_inside_join"}
FIXES = {"runner_interrupt_scored", "chat_timeout_dropped",
         "subprocess_stderr_dropped", "warmup_no_deadline"}

VARIANTS = [
    ("31B bf16 (deployed)", "gemma4-coding:31b",
     "gemma4-coding_31b_audittrap_skeptic_min",
     "gemma4-coding_31b_repohard_np24576_rep1"),
    ("31B QAT ~4bit", "gemma4-coding:31b-qat",
     "gemma4-coding_31b-qat_audittrap_skeptic_min",
     "gemma4-coding_31b-qat_repohard_np24576"),
    ("31B MTP bf16", "gemma4-coding:31b-mtp",
     "gemma4-coding_31b-mtp_audittrap_skeptic_min",
     "gemma4-coding_31b-mtp_repohard_np24576"),
    ("26B-A4B bf16 (ref)", "gemma4-coding:26b-a4b",
     "gemma4-coding_26b-a4b_audittrap_skeptic_min",
     "gemma4-coding_26b-a4b_repohard_np24576_rep2"),
]


def load(kind, tag):
    p = Path(f"results/{kind}/{tag}_latest.json")
    if not p.exists():
        return None
    rows = json.loads(p.read_text())
    return rows if isinstance(rows, list) else [rows]


speed = {}
sp = Path("results/decode_speed_probe.json")
if sp.exists():
    for entry in json.loads(sp.read_text()):
        dec = [r.get("decode_tps") for r in entry.get("short", []) if r.get("decode_tps")]
        pre = [r.get("prefill_tps") for r in entry.get("long", []) if r.get("prefill_tps")]
        if dec:
            speed[entry["model"]] = {
                "decode": statistics.median(dec),
                "prefill": statistics.median(pre) if pre else 0.0,
                "gb": entry.get("size_gb") or 0.0,
            }

print()
print("==== is there a faster way to run the 31B that is still the same model? ====")
print("Speed is the easy half. The gate is whether trap discipline survives: 20/20 is")
print("the bf16 31B under skeptic_min.md, and it is the property that makes it usable.")
print()
h = (f"{'variant':22}{'GB':>6}{'decode':>9}{'vs bf16':>9}{'prefill':>10}"
     f"{'%bus':>6}{'traps':>8}{'fixes':>8}{'claims':>8}{'repohard':>10}")
print(h)
print("-" * len(h))

base_decode = None
for label, model, atag, rtag in VARIANTS:
    s = speed.get(model, {})
    dec = s.get("decode", 0.0)
    gb = s.get("gb", 0.0)
    if label.startswith("31B bf16"):
        base_decode = dec or None
    ratio = f"{dec / base_decode:.2f}x" if (base_decode and dec) else "-"
    bus = f"{100 * dec * gb / PEAK_GBPS:.0f}%" if (dec and gb) else "-"

    rows = load("audittrap", atag)
    def bucket(keep):
        if not rows:
            return "-"
        got = sum(int(r.get("score") or 0) for r in rows if r.get("task") in keep)
        mx = sum(int(r.get("max_score") or 0) for r in rows if r.get("task") in keep)
        return f"{got}/{mx}" if mx else "-"
    traps, fixes = bucket(TRAPS), bucket(FIXES)
    claims = bucket({"claim_battery"})

    rr = load("repohard", rtag)
    repo = f"{sum(int(r.get('score') or 0) for r in rr)}/80" if rr else "-"

    print(f"{label:22}{gb:>6.0f}{dec:>9.2f}{ratio:>9}{s.get('prefill', 0):>10.0f}"
          f"{bus:>6}{traps:>8}{fixes:>8}{claims:>8}{repo:>10}")

print()
print("A variant is only a win if it moves 'decode' up while leaving 'traps' at 20/20")
print("and 'fixes' near 38/40. Speed bought by making the model credulous is not speed,")
print("it is a cheaper way to get wrong answers faster.")
print("==== ALL DONE speed variant gate", flush=True)
PY
echo "==== chain finished $(date) ===="
