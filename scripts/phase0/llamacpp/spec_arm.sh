#!/bin/zsh
# Load the coder with one speculation setting and run both tasks against it.
#
# No screen here: `screen -dmS` started from inside a script silently produced no session and no
# log file, twice, while the identical command run interactively worked. Not worth diagnosing when
# a plain background job with a trap does the same thing and reports its own failures.
set -uo pipefail
ARM="$1"; SPEC="${2:-}"
LS=/Applications/Ollama.app/Contents/Resources/llama-server
GGUF=$HOME/models/qwopus/Qwopus3.6-35B-A3B-Coder-MTP-Q8_0.gguf
lsof -tnP -iTCP:8098 -sTCP:LISTEN | xargs kill 2>/dev/null
sleep 3
$LS -m $GGUF -c 16384 --parallel 1 --port 8098 --host 127.0.0.1 -ngl 99 ${=SPEC} \
    > /tmp/ngram/$ARM.log 2>&1 &
SERVER=$!
trap "kill $SERVER 2>/dev/null" EXIT
code=""
for i in {1..120}; do
  kill -0 $SERVER 2>/dev/null || { echo "$ARM: server exited"; tail -5 /tmp/ngram/$ARM.log; exit 1; }
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 3 http://127.0.0.1:8098/health 2>/dev/null)
  [[ "$code" == "200" ]] && break
  sleep 2
done
[[ "$code" == "200" ]] || { echo "$ARM: never healthy"; tail -5 /tmp/ngram/$ARM.log; exit 1; }
python3 /tmp/ngram/probe.py "$ARM"
python3 /tmp/ngram/prose.py "$ARM"
