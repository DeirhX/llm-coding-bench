#!/bin/zsh
# Kill an in-flight bench task only if it has genuinely stopped progressing.
#
# "No progress" is deliberately hard to trigger. The bench python process sits
# idle on an HTTP read while ollama generates, so its own CPU time is useless as
# a signal, and loading a 63 GB model between runs looks like a stall from the
# outside. So a sample counts as progress if ANY of these moved:
#   - the queue log grew (new task started, run finished)
#   - the newest per-task transcript grew (tokens are arriving)
#   - cumulative CPU time across ollama processes advanced (generating/loading)
# Only when all three are static for the full window do we terminate, and we
# terminate the single bench task, not the queue, so the chain carries on.
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

STALL_SECS="${STALL_SECS:-300}"
SAMPLE_SECS="${SAMPLE_SECS:-30}"
NEEDED=$(( STALL_SECS / SAMPLE_SECS ))
LOG="$ROOT/results/bench_stall_watchdog.log"
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== stall watchdog start $(date) window=${STALL_SECS}s sample=${SAMPLE_SECS}s ===="

fingerprint() {
  local fp=""
  # every chain log, by size
  for f in \
    results/audittrap/local_newmodels_fixes_queue.log \
    results/audittrap/qwopus_budget_probe.log \
    results/audittrap/qwopus_verify.log \
    results/audittrap/gemma_verify.log \
    results/newmodels_other_benches.log \
    results/audittrap/gemma31b_fixmed_rerun.log \
    results/pyhard_qwopus_arms.log \
    results/weak_cell_rescue.log \
    results/audittrap/qwopus_trap_prompt.log \
    results/audittrap/gemma31b_full_suite.log \
    results/repohard_gemma26_stop_arms.log \
    results/gemma_interactive_realism.log \
    results/gemma_repohard_confirm.log \
    results/gemma_native_toolcall.log \
    results/gemma_trap_prompt_small.log \
    results/speed_variant_gate.log \
    results/mtp_full_and_variants.log \
    results/mlxbf16_and_mtp_repeats.log \
    results/mlxbf16_confirm.log
  do
    [[ -f "$f" ]] && fp="$fp $(wc -c < "$f" | tr -d ' ')"
  done
  # newest transcript/artifact under results, by size and mtime
  local newest
  newest="$(find results -type f \( -name '*.txt' -o -name '*.json' \) -newermt '-10 minutes' 2>/dev/null \
            | head -40 | xargs stat -f '%z:%m' 2>/dev/null | sort | tail -3 | tr '\n' ',')"
  fp="$fp|$newest"
  # cumulative cpu time of every ollama process
  local cpu
  cpu="$(ps -eo time,command 2>/dev/null | awk '/[o]llama/ { print $1 }' | tr '\n' ',')"
  fp="$fp|$cpu"
  echo "$fp"
}

bench_pid() {
  # Every bench module, not just audittrap: the repohard arms run as
  # `python -m benches.repohard`, which the old pattern missed, so those runs were
  # never policed at all. The native probe is included too, though it bounds itself
  # with an HTTP timeout and should never need this.
  pgrep -f 'benches\.[a-z]+|run\.py run|gemma_native_toolcall_probe|decode_speed_probe' 2>/dev/null | head -1
}

last=""
stuck=0
while true; do
  sleep "$SAMPLE_SECS"
  pid="$(bench_pid)"
  if [[ -z "$pid" ]]; then
    # chain is between runs or waiting on a gate; nothing to police
    stuck=0
    last=""
    continue
  fi
  now="$(fingerprint)"
  if [[ "$now" == "$last" ]]; then
    stuck=$(( stuck + 1 ))
    echo "-- no progress sample $stuck/$NEEDED pid=$pid $(date)"
  else
    [[ "$stuck" -gt 0 ]] && echo "-- progress resumed after $stuck stalled samples $(date)"
    stuck=0
  fi
  last="$now"

  if [[ "$stuck" -ge "$NEEDED" ]]; then
    echo "==== STALLED ${STALL_SECS}s with no progress, terminating pid=$pid $(date) ===="
    ps -p "$pid" -o pid,etime,command 2>/dev/null
    tail -n 3 results/audittrap/local_newmodels_fixes_queue.log 2>/dev/null
    kill "$pid" 2>/dev/null
    sleep 10
    if kill -0 "$pid" 2>/dev/null; then
      echo "==== SIGTERM ignored, SIGKILL pid=$pid $(date) ===="
      kill -9 "$pid" 2>/dev/null
    fi
    echo "==== KILLED pid=$pid, queue should advance to the next run $(date) ===="
    stuck=0
    last=""
  fi
done
