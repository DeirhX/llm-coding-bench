#!/bin/zsh
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
LOG="$HOME/.ollama/bench/results/pyhard_oss_wrapper.log"
exec >>"$LOG" 2>&1
echo "==== start $(date) ===="
"$HOME/.ollama/bench/run_hard_bench_py.sh" 'gpt-oss:120b'
echo "==== done $(date) ===="
