#!/bin/zsh
# Depth-gate compliance spike: one claude -p run against the resident 31B,
# with a Stop hook that refuses the first answer and demands quoted evidence.
set -uo pipefail

MODEL="gemma4-31b-mtp-96k"
SMALL="gemma3:1b"
OLLAMA_URL="http://127.0.0.1:11434"
ROOT="/Users/deirh/Projects/llm-coding-bench"
OUT=/tmp/spike/out
export SPIKE_OUT="$OUT"
export SPIKE_MAX_BLOCKS="${SPIKE_MAX_BLOCKS:-1}"
LOG=~/.ollama/logs/server.log
BEFORE=$(wc -c < "$LOG")

UNUSED="Workflow,Agent,TaskCreate,TaskUpdate,TaskList,TaskGet,TaskStop,TaskOutput"
UNUSED="$UNUSED,ReportFindings,SendMessage,CronCreate,CronList,CronDelete,ScheduleWakeup"
UNUSED="$UNUSED,EnterWorktree,ExitWorktree,AskUserQuestion,EnterPlanMode,ExitPlanMode,Skill"
UNUSED="$UNUSED,NotebookEdit,Write,Edit,MultiEdit,Bash,WebSearch,WebFetch"

SETTINGS=$(cat <<JSON
{
  "env": {
    "ANTHROPIC_BASE_URL": "$OLLAMA_URL",
    "ANTHROPIC_AUTH_TOKEN": "ollama",
    "ANTHROPIC_API_KEY": "",
    "ANTHROPIC_MODEL": "$MODEL",
    "ANTHROPIC_DEFAULT_MODEL": "$MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "$MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "$MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "$SMALL",
    "ANTHROPIC_SMALL_FAST_MODEL": "$SMALL",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_CODE_ENABLE_AWAY_SUMMARY": "0",
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "90112",
    "API_TIMEOUT_MS": "1800000",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "4096"
  },
  "hooks": { "Stop": [ { "matcher": "", "hooks": [ { "type": "command", "command": "/tmp/spike/hooks/gate.py" } ] } ] },
  "model": "$MODEL",
  "availableModels": ["$MODEL", "$SMALL"],
  "enforceAvailableModels": false
}
JSON
)

export ANTHROPIC_BASE_URL="$OLLAMA_URL"
export ANTHROPIC_AUTH_TOKEN="ollama"
export ANTHROPIC_API_KEY=""
export ANTHROPIC_MODEL="$MODEL"
export ANTHROPIC_DEFAULT_MODEL="$MODEL"
export ANTHROPIC_DEFAULT_SONNET_MODEL="$MODEL"
export ANTHROPIC_DEFAULT_OPUS_MODEL="$MODEL"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="$SMALL"
export ANTHROPIC_SMALL_FAST_MODEL="$SMALL"
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
export CLAUDE_CODE_ENABLE_AWAY_SUMMARY=0
export CLAUDE_CODE_MAX_CONTEXT_TOKENS=90112
export API_TIMEOUT_MS=1800000
export CLAUDE_CODE_MAX_OUTPUT_TOKENS=4096

PROMPT='In this repository, when a benchmark task exceeds its wall-clock budget, what happens to the output the model had already produced by then? Answer precisely.'

cd "$ROOT" || exit 1
START=$(date +%s)
claude -p "$PROMPT" --model "$MODEL" --settings "$SETTINGS" \
  --disallowed-tools "$UNUSED" \
  --output-format json --dangerously-skip-permissions \
  > "$OUT/spike.result.json" 2> "$OUT/spike.stderr"
RC=$?
END=$(date +%s)
echo "rc=$RC wall=$((END-START))s"
tail -c +$((BEFORE+1)) "$LOG" > "$OUT/spike.serverlog"
