#!/bin/zsh
# Phase 0 cheap-tier driver: one claude -p run against the resident qwen3:4b.
set -u
TEST="$1"; shift
PROMPT="$1"; shift
HOOKS_JSON="${1:-}"
OUT=/tmp/phase0/out
export PHASE0_OUT="$OUT"
LOG=~/.ollama/logs/server.log
BEFORE=$(wc -c < "$LOG")

SETTINGS=$(cat <<JSON
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:11434",
    "ANTHROPIC_AUTH_TOKEN": "ollama",
    "ANTHROPIC_API_KEY": "",
    "ANTHROPIC_MODEL": "qwen3:4b",
    "ANTHROPIC_DEFAULT_MODEL": "qwen3:4b",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "qwen3:4b",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "qwen3:4b",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "qwen3:4b",
    "ANTHROPIC_SMALL_FAST_MODEL": "qwen3:4b",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_CODE_ENABLE_AWAY_SUMMARY": "0",
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "30000",
    "API_TIMEOUT_MS": "600000",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "2048"
  },
  $HOOKS_JSON
  "model": "qwen3:4b",
  "availableModels": ["qwen3:4b"],
  "enforceAvailableModels": false
}
JSON
)
export ANTHROPIC_BASE_URL=http://127.0.0.1:11434
export ANTHROPIC_AUTH_TOKEN=ollama
export ANTHROPIC_API_KEY=""
export ANTHROPIC_MODEL=qwen3:4b
export ANTHROPIC_SMALL_FAST_MODEL=qwen3:4b
export ANTHROPIC_DEFAULT_HAIKU_MODEL=qwen3:4b
export API_TIMEOUT_MS=600000
export CLAUDE_CODE_MAX_OUTPUT_TOKENS=2048
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
export CLAUDE_CODE_ENABLE_AWAY_SUMMARY=0

cd /tmp/phase0/cc || exit 1
START=$(date +%s)
claude -p "$PROMPT" --model qwen3:4b --settings "$SETTINGS" \
  --output-format json --dangerously-skip-permissions \
  > "$OUT/$TEST.result.json" 2> "$OUT/$TEST.stderr"
RC=$?
END=$(date +%s)
echo "test=$TEST rc=$RC wall=$((END-START))s"
tail -c +$((BEFORE+1)) "$LOG" > "$OUT/$TEST.serverlog"
