#!/bin/zsh
# Both windows, all arms, in one sequential process.
#
# The first attempt at this used a second screen session gated on the first disappearing,
# and the gate misfired instantly: `screen -ls | rg -q` takes SIGPIPE on the producer when
# rg exits early on a match, and `set -o pipefail` turns that into a false condition. The
# 128k stage therefore started while the 64k stage was mid-measurement, Ollama evicted a
# resident 62GB model to make room, and the draft arm's numbers became a record of the
# eviction. This repo has hit that exact pipefail-plus-quiet-grep trap before.
#
# The fix is to remove the coordination rather than repair it: one process, two invocations,
# nothing to synchronise. Slower to write results, impossible to race.

set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"

for w in 64k 128k; do
  echo
  echo "################ window $w  $(date) ################"
  "$PY" -u scripts/speculation_acceptance_probe.py "$w" || echo "---- FAILED $w rc=$? ----" >&2
done

echo
echo "==== ALL DONE speculation acceptance $(date) ===="
