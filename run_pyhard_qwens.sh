#!/bin/zsh
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
LOG="$HOME/.ollama/bench/results/pyhard_qwens_wrapper.log"
exec >>"$LOG" 2>&1
echo "==== start $(date) ===="
"$HOME/.ollama/bench/run_hard_bench_py.sh" \
  'qwen3-coder-next:q8_0' \
  'qwen3-coder:30b-a3b-fp16'
echo "==== done $(date) ===="
