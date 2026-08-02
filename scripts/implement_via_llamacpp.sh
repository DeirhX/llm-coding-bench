#!/bin/zsh
# Serve the coder GGUF through llama-server with n-gram speculation, and put the Anthropic proxy in
# front of it so Claude Code -- and therefore the depth pipeline -- can drive it unchanged.
#
# This is not the same model the pipeline was built around. There is no GGUF of gemma4-31b here, and
# the MLX runner Ollama uses for it cannot speculate, so measuring speculation at all means changing
# model as well. Read any timing from this as "the proxy path works and costs X with a 35B A3B
# coder", not as "gemma got faster".
#
# It also needs the GPU to itself: the resident gemma is 93 GB and this is another 37, so the caller
# is expected to have evicted it and to put it back afterwards.
set -uo pipefail
LS=/Applications/Ollama.app/Contents/Resources/llama-server
GGUF=$HOME/models/qwopus/Qwopus3.6-35B-A3B-Coder-MTP-Q8_0.gguf
CTX=${CTX:-32768}
# llama.cpp builds a tool-call parser by rendering the model's own template against synthetic
# conversations, and this coder's template raises on a system message that is not first -- which one
# of those probes always is. Every request carrying tools then fails with a 400 about parser
# generation, before a single one of our messages is looked at. Point TEMPLATE at a copy with that
# one raise removed; the position it guards is one we satisfy anyway.
TEMPLATE=${TEMPLATE:-}
LPORT=8098
PPORT=8099
mkdir -p /tmp/implement-spec
# zsh does not word-split ${VAR:+--flag $VAR}, so the whole thing arrives as one argument and
# llama-server rejects it as an unknown option that happens to contain a space.
TEMPLATE_ARG=()
[[ -n "$TEMPLATE" ]] && TEMPLATE_ARG=(--chat-template-file "$TEMPLATE")

lsof -tnP -iTCP:$LPORT -sTCP:LISTEN | xargs kill 2>/dev/null
lsof -tnP -iTCP:$PPORT -sTCP:LISTEN | xargs kill 2>/dev/null
sleep 2

# ngram-mod was the faster of the two n-gram arms on editing tasks in §7 and needs no draft model,
# which matters here because the draft would want memory this does not have.
$LS -m $GGUF -c $CTX --parallel 1 --port $LPORT --host 127.0.0.1 -ngl 99 \
    --spec-type ngram-mod --swa-full "${TEMPLATE_ARG[@]}" \
    > /tmp/implement-spec/server.log 2>&1 &
SERVER=$!
for i in {1..180}; do
  kill -0 $SERVER 2>/dev/null || { echo "server exited"; tail -20 /tmp/implement-spec/server.log; exit 1; }
  [[ "$(curl -s -o /dev/null -w '%{http_code}' -m 3 http://127.0.0.1:$LPORT/health)" == "200" ]] && break
  sleep 2
done
echo "llama-server up on $LPORT (pid $SERVER)"

python3 "$(dirname "$0")/anthropic_proxy.py" --port $PPORT --upstream http://127.0.0.1:$LPORT/v1/chat/completions \
    --force-model qwopus > /tmp/implement-spec/proxy.log 2>&1 &
PROXY=$!
sleep 2
echo "proxy up on $PPORT (pid $PROXY)"
echo "export ANTHROPIC_BASE_URL=http://127.0.0.1:$PPORT"
wait $SERVER
