#!/usr/bin/env bash
#
# Makes Ollama hold a loaded model for 8 hours instead of 5 minutes.
#
# WHY THIS EXISTS AT THE SERVER RATHER THAN IN A REQUEST
#
# Claude Code talks to Ollama's Anthropic-compatible endpoint, and that endpoint ignores
# keep_alive: a POST to /v1/messages carrying keep_alive "7h" returned 200 and left the
# model expiring in four minutes. /api/chat honours the field, which is why the launcher's
# warm-up shows 8 hours -- and why the first real turn immediately knocks it back to the
# server default. Nothing in the request path can fix that, so the default itself has to
# change. OLLAMA_KEEP_ALIVE supplies it for every request that omits the field.
#
# The cost of not doing this: a 62 GB model unloads after five idle minutes, and the next
# turn pays a cold load plus a full prefill of the whole conversation. Measured on a 115k
# conversation that is roughly four minutes before the first token.
#
# A previous attempt solved this with a heartbeat in the logging proxy, re-arming keep_alive
# every 120 seconds. It worked, and it also resurrected models: a deliberate `ollama stop`
# was undone within seconds, because a keep_alive request against an unloaded model loads it.
# A server default cannot do that, since it only applies to requests that were coming anyway.
#
# GUI apps do not inherit a shell's environment, so the value goes in via launchctl, and a
# LaunchAgent re-applies it at login because `launchctl setenv` does not survive a reboot.
set -uo pipefail

PROBE_MODEL="${OLLAMA_KEEPALIVE_PROBE:-gemma3:1b}"

# Checks the only thing that matters: what expiry a request that omits keep_alive receives.
# Reading launchctl would say what was set, not what the running server believes.
if [[ "${1:-}" == "--check" ]]; then
  echo "probing with $PROBE_MODEL (no keep_alive in the request)..."
  curl -sf -o /dev/null --max-time 300 -X POST http://127.0.0.1:11434/api/chat \
    -H 'content-type: application/json' \
    -d "{\"model\":\"$PROBE_MODEL\"}" \
    || { echo "error: could not load $PROBE_MODEL" >&2; exit 1; }
  curl -sf --max-time 10 http://127.0.0.1:11434/api/ps | python3 -c '
import json, sys
from datetime import datetime, timezone

models = json.load(sys.stdin).get("models") or []
probe = sys.argv[1]
for m in models:
    name = m.get("name") or m.get("model") or ""
    if not name.startswith(probe.split(":")[0]):
        continue
    raw = (m.get("expires_at") or "").replace("Z", "+00:00")
    try:
        left = datetime.fromisoformat(raw) - datetime.now(timezone.utc)
    except ValueError:
        print(f"{name}: unparseable expires_at {raw!r}")
        continue
    mins = left.total_seconds() / 60
    verdict = "PASS" if mins > 60 else "FAIL -- server default is still short"
    print(f"{name}: expires in {mins:.0f} min  {verdict}")
    break
else:
    print("probe model not resident, cannot judge")
' "$PROBE_MODEL"
  exit 0
fi

KEEP_ALIVE="${1:-8h}"
LABEL="local.ollama.keepalive"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

echo "setting OLLAMA_KEEP_ALIVE=$KEEP_ALIVE"
launchctl setenv OLLAMA_KEEP_ALIVE "$KEEP_ALIVE"

mkdir -p "$(dirname "$PLIST")"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/launchctl</string>
        <string>setenv</string>
        <string>OLLAMA_KEEP_ALIVE</string>
        <string>${KEEP_ALIVE}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
PLIST_EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST" 2>/dev/null || true
echo "installed $PLIST"

# The server reads its environment once at start, so a running instance keeps the old
# default. Restarting unloads whatever is resident: run this when nothing matters.
if pgrep -qf 'Ollama.app' 2>/dev/null || pgrep -qf 'ollama serve' 2>/dev/null; then
  echo "restarting Ollama so the server picks it up..."
  osascript -e 'quit app "Ollama"' 2>/dev/null || pkill -f 'Ollama.app' 2>/dev/null || true
  for _ in $(seq 1 30); do
    pgrep -qf 'ollama serve' 2>/dev/null || break
    sleep 1
  done
  open -a Ollama
  for _ in $(seq 1 60); do
    curl -sf -o /dev/null --max-time 2 http://127.0.0.1:11434/api/version && break
    sleep 1
  done
fi

echo "ollama version: $(curl -sf --max-time 5 http://127.0.0.1:11434/api/version || echo unreachable)"
echo
echo "verify with: scripts/ollama-keepalive.sh --check"
