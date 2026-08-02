#!/bin/zsh
# Subagent cost probe: one Claude Code session on the resident 31B that delegates once.
# Measures what a subagent inherits from the parent's prefix cache and whether the
# parent's context survives the delegation.
set -u
TEST="$1"; shift
PROMPT="$1"; shift
MODEL=gemma4-31b-mtp-96k
OUT=/tmp/phase0/out
export PHASE0_OUT="$OUT"
LOG=~/.ollama/logs/server.log
BEFORE=$(wc -c < "$LOG")

HOOKS='"hooks": {
    "Stop": [ { "hooks": [ { "type": "command", "command": "/tmp/phase0/hooks/rec.py" } ] } ],
    "SubagentStop": [ { "hooks": [ { "type": "command", "command": "/tmp/phase0/hooks/rec.py" } ] } ],
    "PreToolUse": [ { "matcher": "Agent", "hooks": [ { "type": "command", "command": "/tmp/phase0/hooks/rec.py" } ] } ],
    "PostToolUse": [ { "matcher": "Agent|Read", "hooks": [ { "type": "command", "command": "/tmp/phase0/hooks/rec.py" } ] } ]
  },'

SETTINGS=$(cat <<JSON
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:11434",
    "ANTHROPIC_AUTH_TOKEN": "ollama",
    "ANTHROPIC_API_KEY": "",
    "ANTHROPIC_MODEL": "$MODEL",
    "ANTHROPIC_DEFAULT_MODEL": "$MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "$MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "$MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "$MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL": "$MODEL",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_CODE_ENABLE_AWAY_SUMMARY": "0",
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "90000",
    "API_TIMEOUT_MS": "1800000",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "4096"
  },
  $HOOKS
  "model": "$MODEL",
  "availableModels": ["$MODEL"],
  "enforceAvailableModels": false
}
JSON
)
export ANTHROPIC_BASE_URL=http://127.0.0.1:11434 ANTHROPIC_AUTH_TOKEN=ollama ANTHROPIC_API_KEY=""
export ANTHROPIC_MODEL=$MODEL ANTHROPIC_SMALL_FAST_MODEL=$MODEL ANTHROPIC_DEFAULT_HAIKU_MODEL=$MODEL
export API_TIMEOUT_MS=1800000 CLAUDE_CODE_MAX_OUTPUT_TOKENS=4096
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 CLAUDE_CODE_ENABLE_AWAY_SUMMARY=0

cd /tmp/phase0/cc || exit 1
START=$(date +%s)
claude -p "$PROMPT" --model "$MODEL" --settings "$SETTINGS" \
  --output-format json --dangerously-skip-permissions \
  > "$OUT/$TEST.result.json" 2> "$OUT/$TEST.stderr"
RC=$?
echo "test=$TEST rc=$RC wall=$(( $(date +%s) - START ))s"
tail -c +$((BEFORE+1)) "$LOG" > "$OUT/$TEST.serverlog"
