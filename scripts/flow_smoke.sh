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
#
# `n_ctx` is not at the top of /props and never was: llama-server reports it inside
# `default_generation_settings`, which is the one slot's window and, at total_slots=1, the whole
# one. Read from the top level it came back empty on every run so far, the fallback below took
# over, and the number this script printed as measured was a constant that happened to be right
# once. So: both places, and say which one answered.
CTX=${FLOW_CTX:-$(curl -fsS -m 5 http://127.0.0.1:8098/props 2>/dev/null |
  "$ROOT/.venv/bin/python" -c 'import json,sys
d = json.load(sys.stdin)
gen = d.get("default_generation_settings") or {}
print(d.get("n_ctx") or gen.get("n_ctx") or 0)' 2>/dev/null)}
if [[ -z "$CTX" || "$CTX" -le 8192 ]]; then
  CTX=98304
  print -u2 "warning: llama-server did not say its window; assuming $CTX tokens"
fi
# Just under two thirds, and the number is derived rather than chosen. The client refuses to send
# past what it is told, so this is the ceiling a session actually reaches -- but it counts the way
# every estimator counts, four characters to a token, and none of this material is prose. Twelve real
# transcripts measured against llama-server's own tokeniser came back between 2.76 and 3.21
# characters per token, so an estimate can be 1.45x low.
#
# At three quarters that is fatal arithmetic: 98,304 declared, believed, is 142,000 tokens sent into
# a 131,072-token window. Runs 18, 20 and 25 each died a few thousand tokens past the end while
# believing themselves comfortably inside a budget, and each time it was written off as the client
# and the runner disagreeing by 10%. They disagree by 45%.
#
# 65% survives the worst ratio measured with 7,500 tokens to spare, whether or not compaction ever
# fires. It costs a session a fifth of the window it could have addressed. An overflow costs the run.
DECLARED=$(( CTX * 65 / 100 ))

mkdir -p "$OUT"
SETTINGS="$OUT/settings.json"
# The guard is told the window the client believes in, not the one llama-server has. The client is
# what dies: it refuses to send above CLAUDE_CODE_MAX_CONTEXT_TOKENS, and a guard measuring against the
# larger real window would keep saying there is room right up to the error. Unset, it defaulted to
# 98,304, which happened to equal three quarters of a 131,072-token server -- agreement by coincidence,
# which stops the day the server is started with anything else.
GUARD="$ROOT/scripts/cc-context-guard.py --stop-advice answer --window $DECLARED"
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
# And the poll. Run 25's parent called TaskOutput ten times, each returning 32,164 characters of the
# stage's working record, and died of a full window with its last round still working.
DISALLOW="$DISALLOW,TaskOutput"

ANTHROPIC_BASE_URL="$BASE" ANTHROPIC_API_KEY=local \
  claude -p "$TASK" --model "$MODEL" --settings "$SETTINGS" \
  --session-id "$SESSION" --output-format json --disallowed-tools "$DISALLOW" \
  --dangerously-skip-permissions > "$OUT/answer.json" 2> "$OUT/stderr.log"
echo "claude exited $?"

python3 "$ROOT/scripts/cc-flow-status.py" --session "$SESSION" --root "$ROOT"
