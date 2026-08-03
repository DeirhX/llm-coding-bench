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

rm -f /tmp/cc-guard-off /tmp/cc-depth-off  # a stale one silently disables every guard
BASE=${ANTHROPIC_BASE_URL:-http://127.0.0.1:8099}
MODEL=${FLOW_MODEL:-qwopus}
OUT=${FLOW_OUT:-/tmp/flow-smoke}
TASK=${1:-"review: the long-sleep rule in scripts/cc-context-guard.py. Is it correct, and can a stage that needs to wait for something legitimately get past it?"}

# A run against a dead proxy does not fail, it hangs: the client waits on a connection nobody will
# answer and the log stays at the last thing that worked. Worse, the proxy dies quietly -- it is
# killed with the process group of whatever spawned it, which is why it belongs in its own screen
# and why this asks before spending an hour finding out.
if ! curl -fsS -m 5 -o /dev/null "$BASE/v1/models"; then
  print -u2 "no model server answering at $BASE -- start the proxy first, detached:"
  print -u2 "  screen -dmS proxy $ROOT/.venv/bin/python $ROOT/scripts/anthropic_proxy.py --port 8099 \\"
  print -u2 "    --upstream http://127.0.0.1:8098/v1/chat/completions --force-model $MODEL"
  exit 1
fi

# The window the client is allowed to believe in. Unset, Claude Code assumes 200k, never compacts,
# and hands the server a prompt it must refuse: run 18 died at 98,342 tokens against a window of
# 98,304 -- 38 tokens over, after 135 turns and an hour of work -- and the refusal arrives as a 502
# the client treats as fatal. Asked for rather than assumed, because the answer is whatever
# llama-server was started with, and headroom because the two of them count tokens differently.
CTX=${FLOW_CTX:-$(curl -fsS -m 5 http://127.0.0.1:8098/props 2>/dev/null |
  "$ROOT/.venv/bin/python" -c 'import json,sys; d=json.load(sys.stdin); print(d.get("n_ctx") or 0)' \
  2>/dev/null)}
[[ -n "$CTX" && "$CTX" -gt 8192 ]] || CTX=98304
DECLARED=$(( CTX - 8192 ))

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
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "$DECLARED",
    "API_TIMEOUT_MS": "1800000"
  },
  "hooks": {
    "SessionStart": [ { "hooks": [ { "type": "command", "command": "$CONTRACT" } ] } ],
    "UserPromptSubmit": [ { "hooks": [ { "type": "command", "command": "$CONTRACT" } ] } ],
    "PreToolUse": [
      { "hooks": [ { "type": "command", "command": "$FLOW" } ] },
      { "matcher": "Read|Bash|WebFetch|WebSearch|Write|Edit|MultiEdit", "hooks": [ { "type": "command", "command": "$GUARD" } ] }
    ],
    "PostToolUse": [
      { "matcher": "Task|Agent|TaskList|TaskOutput", "hooks": [ { "type": "command", "command": "$FLOW" } ] }
    ],
    "Stop": [ { "hooks": [ { "type": "command", "command": "$GATE" } ] } ],
    "SubagentStop": [ { "hooks": [ { "type": "command", "command": "$GATE" } ] } ]
  }
}
JSON

SESSION=$(python3 -c 'import uuid; print(uuid.uuid4())')
echo "session $SESSION, model $MODEL via $BASE"
echo "$SESSION" > "$OUT/session"

# An implement flow writes, so it is pointed at a detached worktree rather than at somebody's
# uncommitted work: FLOW_CWD is where the session runs, and the hooks take their root from it.
cd "${FLOW_CWD:-$ROOT}"
# The same lean tool list `claude-gemma.sh --flows` uses, minus the Task tools a flow needs. Without
# it the client offers ReportFindings, and a session used it twice to file three findings into a
# channel nothing here reads -- then finished on a prose summary the gate judged as citing nothing.
DISALLOW="Workflow,ReportFindings,SendMessage,CronCreate,CronList,CronDelete,ScheduleWakeup"
DISALLOW="$DISALLOW,EnterWorktree,ExitWorktree,AskUserQuestion,EnterPlanMode,ExitPlanMode,Skill"

ANTHROPIC_BASE_URL="$BASE" ANTHROPIC_API_KEY=local \
  claude -p "$TASK" --model "$MODEL" --settings "$SETTINGS" \
  --session-id "$SESSION" --output-format json --disallowed-tools "$DISALLOW" \
  --dangerously-skip-permissions > "$OUT/answer.json" 2> "$OUT/stderr.log"
echo "claude exited $?"

python3 "$ROOT/scripts/cc-flow-status.py" --session "$SESSION" --root "$ROOT"
