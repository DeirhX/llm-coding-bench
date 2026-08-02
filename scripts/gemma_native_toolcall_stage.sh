#!/usr/bin/env zsh
# Waits for the gemma chain to drain, rebuilds the tuned models so they carry the
# <|tool_response> stop marker, then probes the native tool-calling path.
#
# Why this stage exists: every score in this repo was measured on the bench's text tool
# protocol. Ollama renders gemma4 with a compiled Go renderer, and under a native tools
# request it uses <|tool_call>/<|tool_response> instead. An agent CLI behind a proxy
# takes that second path, so "does it hang in Claude Code" is not answered by any
# existing arm.

set -u
set -o pipefail

cd "${0:A:h}/.."
PY=".venv/bin/python"
LOG="results/gemma_native_toolcall.log"
PREV_LOG="results/gemma_repohard_confirm.log"
DONE_MARK="ALL DONE"

mkdir -p results
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== gemma native tool-calling stage $(date) ===="

if [[ "${BENCH_SKIP_GATE:-0}" == "1" ]]; then
  echo "==== gate skipped by request $(date) ===="
else
  for i in $(seq 1 8640); do
    grep -q "$DONE_MARK" "$PREV_LOG" 2>/dev/null && { echo "==== chain drained, proceeding $(date) ===="; break; }
    sleep 10
  done
  if ! grep -q "$DONE_MARK" "$PREV_LOG" 2>/dev/null; then
    echo "==== chain never drained after 24h, aborting $(date) ====" >&2
    exit 2
  fi
fi

# Free the GPU before rebuilding, so the new blobs are not racing a resident copy.
for m in gemma4-coding:26b-a4b gemma4-coding:31b gemma4:26b-a4b-it-bf16 gemma4:31b-it-bf16; do
  ollama stop "$m" 2>/dev/null || true
done

echo
echo "---- rebuilding tuned models with the <|tool_response> stop $(date) ----"
ollama create gemma4-coding:26b-a4b -f modelfiles/gemma4-26b-a4b-coding.Modelfile || exit 3
ollama create gemma4-coding:31b     -f modelfiles/gemma4-31b-coding.Modelfile     || exit 3

echo
echo "---- confirming the stop list survived the rebuild ----"
for m in gemma4-coding:26b-a4b gemma4-coding:31b; do
  echo "  $m:"
  ollama show --modelfile "$m" 2>/dev/null | grep -i '^PARAMETER' | sed 's/^/    /'
done

echo
echo "---- native tool-calling probe $(date) ----"
# Stock first: if the fabrication does not reproduce on this path at all, the tuned
# arms have nothing to fix and the stop marker is untestable rather than working.
"$PY" -u scripts/gemma_native_toolcall_probe.py \
  --models gemma4:26b-a4b-it-bf16 gemma4-coding:26b-a4b gemma4:31b-it-bf16 gemma4-coding:31b \
  --repeats 2 --max-rounds 15 --timeout 900 \
  --out results/gemma_native_toolcall_probe.json \
  || echo "---- probe FAILED rc=$? ----" >&2

echo
echo "==== ALL DONE $(date) ===="
