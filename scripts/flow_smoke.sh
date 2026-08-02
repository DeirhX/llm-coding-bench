#!/bin/zsh
# Drive an interactive-shaped flow session headlessly, to see whether the machinery holds.
#
# This is the same wiring `claude-gemma.sh --flows` produces -- the contract hook that starts a
# flow, the Task hook that sequences it, the gate on Stop and SubagentStop -- but with `-p` instead
# of a terminal, so a run can be watched from a log and repeated. It exists because the parts it
# exercises are hooks, and a hook that is wrong is silently wrong: the session simply proceeds
# without it and looks fine.
#
# It points at whatever ANTHROPIC_BASE_URL says, defaulting to the proxy in front of llama-server,
# because the question here is whether the flow sequences correctly and that is not a question about
# which weights answer.
set -uo pipefail
ROOT=${0:a:h:h}
BASE=${ANTHROPIC_BASE_URL:-http://127.0.0.1:8099}
MODEL=${FLOW_MODEL:-qwopus}
OUT=${FLOW_OUT:-/tmp/flow-smoke}
TASK=${1:-"review: the long-sleep rule in scripts/cc-context-guard.py. Is it correct, and can a stage that needs to wait for something legitimately get past it?"}

mkdir -p "$OUT"
SETTINGS="$OUT/settings.json"
GUARD="$ROOT/scripts/cc-context-guard.py --stop-advice answer"
FLOW="$ROOT/scripts/cc-flow-guard.py"
GATE="$ROOT/scripts/cc-depth-gate.py"
CONTRACT="$ROOT/scripts/cc-depth-contract.py --adapter review"

cat > "$SETTINGS" <<JSON
{
  "model": "$MODEL",
  "availableModels": ["$MODEL"],
  "enforceAvailableModels": false,
  "env": {
    "ANTHROPIC_BASE_URL": "$BASE",
    "ANTHROPIC_AUTH_TOKEN": "local",
    "ANTHROPIC_MODEL": "$MODEL",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
  },
  "hooks": {
    "SessionStart": [ { "hooks": [ { "type": "command", "command": "$CONTRACT" } ] } ],
    "UserPromptSubmit": [ { "hooks": [ { "type": "command", "command": "$CONTRACT" } ] } ],
    "PreToolUse": [
      { "matcher": "Read|Bash|WebFetch|WebSearch", "hooks": [ { "type": "command", "command": "$GUARD" } ] },
      { "hooks": [ { "type": "command", "command": "$FLOW" } ] }
    ],
    "Stop": [ { "hooks": [ { "type": "command", "command": "$GATE" } ] } ],
    "SubagentStop": [ { "hooks": [ { "type": "command", "command": "$GATE" } ] } ]
  }
}
JSON

SESSION=$(python3 -c 'import uuid; print(uuid.uuid4())')
echo "session $SESSION, model $MODEL via $BASE"
echo "$SESSION" > "$OUT/session"

cd "$ROOT"
ANTHROPIC_BASE_URL="$BASE" ANTHROPIC_API_KEY=local \
  claude -p "$TASK" --model "$MODEL" --settings "$SETTINGS" \
  --session-id "$SESSION" --output-format json \
  --dangerously-skip-permissions > "$OUT/answer.json" 2> "$OUT/stderr.log"
echo "claude exited $?"

python3 "$ROOT/scripts/cc-flow-status.py" --session "$SESSION" --root "$ROOT"
