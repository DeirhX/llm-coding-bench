#!/usr/bin/env bash
#
# Free the GPU, in the order that actually works.
#
# Three things make this harder than "ollama stop":
#
#   1. ollama stop is silently refused while a request is in flight. It prints a
#      spinner, exits 0, and the model stays resident with an expiry of -0 min.
#   2. Killing the runner process works, and is undone in about one second: the
#      server respawns it to serve the client that is still retrying, reloads 62 GB
#      and starts the same prefill from zero. Measured: killed at 12:37:09, new
#      runner at 12:37:10, prefill restarted at 12:37:17.
#   3. So the client has to go first. Any client, not just Claude Code, which is why
#      this looks at who holds a socket to port 11434 rather than grepping for a
#      process name.
#
# Killing someone else's client is destructive, so it needs --clients said out loud,
# and anything this script cannot determine makes it refuse rather than proceed. An
# earlier version used mapfile, which macOS bash 3.2 does not have; the client list
# came back empty, the safety check passed vacuously, and it evicted a model out from
# under a live session. Hence: no bash 4 builtins, and no assuming an empty list
# means an empty list.
#
# usage: evict.sh [--force] [--clients] [--dry-run] [model ...]
#   --force    evict even while work is in flight
#   --clients  kill the processes connected to Ollama first
#   --dry-run  report what would happen and change nothing
#   model ...  restrict to these models (default: everything resident)
set -uo pipefail

HOST="http://127.0.0.1:11434"
# Resolve the symlink chain before taking the directory: launched through a symlink,
# dirname would point at the link's directory and state.py would not be found. macOS
# has no readlink -f, so the chain is walked by hand.
SELF="${BASH_SOURCE[0]}"
while [[ -L "$SELF" ]]; do
  TARGET="$(readlink "$SELF")"
  case "$TARGET" in
    /*) SELF="$TARGET" ;;
    *) SELF="$(cd "$(dirname "$SELF")" && pwd)/$TARGET" ;;
  esac
done
HERE="$(cd "$(dirname "$SELF")" && pwd)"
FORCE=0
KILL_CLIENTS=0
DRY=0
WANTED=""

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --clients) KILL_CLIENTS=1 ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,28p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) WANTED="$WANTED $arg" ;;
  esac
done

say() { printf '%s\n' "$*"; }
act() { if (( DRY )); then say "would: $*"; else eval "$@"; fi; }

say "=== before ==="
python3 "$HERE/state.py"
say ""

STATE="$(python3 "$HERE/state.py" --json 2>/dev/null)"
if [[ -z "$STATE" ]]; then
  say "Refusing: cannot read the state script, so nothing here can be judged safe."
  exit 1
fi
VERDICT="$(printf '%s' "$STATE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["verdict"])')"
ETA="$(printf '%s' "$STATE" | python3 -c '
import json, sys
print((json.load(sys.stdin).get("burst") or {}).get("eta_s") or 0)')"

if [[ "$VERDICT" == PREFILLING* || "$VERDICT" == BUSY* ]] && (( FORCE == 0 )); then
  say "Refusing: $VERDICT."
  if [[ "$ETA" -gt 0 ]]; then
    say "About $((ETA / 60)) min $((ETA % 60))s of prompt processing would be thrown"
    say "away and repeated from zero when the client retries."
  fi
  say "Re-run with --force if that is what you want."
  exit 1
fi

# Who is talking to Ollama? The client end of a connection shows ->:11434.
# lsof missing or failing is treated as "unknown", never as "none".
if ! command -v lsof >/dev/null 2>&1; then
  say "Refusing: lsof is unavailable, so connected clients cannot be identified."
  exit 1
fi
CONNS="$(lsof -nP -iTCP:11434 -sTCP:ESTABLISHED 2>/dev/null)"
LSOF_RC=$?
if (( LSOF_RC > 1 )); then
  say "Refusing: lsof failed (exit $LSOF_RC), so connected clients are unknown."
  exit 1
fi
SERVER_PIDS=" $(pgrep -f 'ollama serve' 2>/dev/null | tr '\n' ' ') "

CLIENT_PIDS=""
while IFS= read -r pid; do
  [[ -z "$pid" ]] && continue
  [[ "$SERVER_PIDS" == *" $pid "* ]] && continue
  case " $CLIENT_PIDS " in *" $pid "*) continue ;; esac
  CLIENT_PIDS="$CLIENT_PIDS $pid"
done <<< "$(printf '%s\n' "$CONNS" | awk '/->127\.0\.0\.1:11434/ {print $2}')"
CLIENT_PIDS="${CLIENT_PIDS# }"

# Between requests a session holds no socket, so processes that will come back count too.
IDLE_PIDS=""
while IFS= read -r pid; do
  [[ -z "$pid" ]] && continue
  case " $CLIENT_PIDS " in *" $pid "*) continue ;; esac
  case " $IDLE_PIDS " in *" $pid "*) continue ;; esac
  IDLE_PIDS="$IDLE_PIDS $pid"
done <<< "$(ps -Awwo pid,command 2>/dev/null | awk '
  /evict\.sh|ollama-evict|awk/ { next }
  /claude --model|claude --settings|ANTHROPIC_BASE_URL/ { print $1; next }
  /_probe\.py|benches\/|bench\.py/ { print $1 }
')"
IDLE_PIDS="${IDLE_PIDS# }"

if [[ -n "$IDLE_PIDS" ]]; then
  say "clients that are idle now but will return:"
  for pid in $IDLE_PIDS; do
    say "  pid $pid  $(ps -p "$pid" -o command= 2>/dev/null | cut -c1-70)"
  done
  if (( KILL_CLIENTS == 0 )); then
    say ""
    say "Refusing: a session between turns holds no socket, but its next turn reloads"
    say "the model. Re-run with --clients to kill them, or stop them yourself first."
    exit 1
  fi
  CLIENT_PIDS="${CLIENT_PIDS:+$CLIENT_PIDS }$IDLE_PIDS"
fi

if [[ -n "$CLIENT_PIDS" ]]; then
  say "clients connected to Ollama:"
  for pid in $CLIENT_PIDS; do
    say "  pid $pid  $(ps -p "$pid" -o command= 2>/dev/null | cut -c1-70)"
  done
  if (( KILL_CLIENTS == 0 )); then
    say ""
    say "Refusing: these will reload the model within seconds of it being evicted."
    say "Re-run with --clients to kill them, or stop them yourself first."
    exit 1
  fi
  say "killing them..."
  for pid in $CLIENT_PIDS; do act "kill -TERM $pid 2>/dev/null || true"; done
  (( DRY )) || sleep 4
  for pid in $CLIENT_PIDS; do
    if ! (( DRY )) && kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
  done
  (( DRY )) || sleep 2
fi

# Ask politely first: a clean unload is cheaper than a killed runner.
RESIDENT="$(curl -sf --max-time 10 "$HOST/api/ps" | python3 -c '
import json, sys
for m in json.load(sys.stdin).get("models") or []:
    name = m.get("name") or m.get("model")
    if name:
        print(name)' 2>/dev/null)"

if [[ -z "$RESIDENT" ]]; then
  say "nothing resident, nothing to evict"
else
  while IFS= read -r model; do
    [[ -z "$model" ]] && continue
    if [[ -n "$WANTED" ]]; then
      keep=0
      for w in $WANTED; do [[ "$model" == *"$w"* ]] && keep=1; done
      (( keep )) || continue
    fi
    say "ollama stop $model"
    act "ollama stop '$model' >/dev/null 2>&1 || true"
  done <<< "$RESIDENT"
  (( DRY )) || sleep 4

  if pgrep -qf 'ollama runner'; then
    say "still resident after stop, killing the runner"
    act "pkill -9 -f 'ollama runner' || true"
    (( DRY )) || sleep 5
  fi
fi

say ""
say "=== after ==="
python3 "$HERE/state.py"
if curl -sf --max-time 5 "$HOST/api/version" >/dev/null; then
  say "server: healthy"
else
  say "server: UNREACHABLE -- restart the Ollama app"
fi
