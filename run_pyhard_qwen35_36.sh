#!/bin/zsh
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
LOG="$HOME/.ollama/bench/results/pyhard_qwen35_36_wrapper.log"
exec >>"$LOG" 2>&1
echo "==== start $(date) ===="
"$HOME/.ollama/bench/run_hard_bench_py.sh" \
  'qwen3.5:35b-a3b-coding-bf16' \
  'qwen3.6:35b-a3b-coding-bf16'
echo "==== done $(date) ===="
