#!/bin/zsh
# One arm: prefill, save the slot, kill the server, start it again, restore, and time the answer.
# A restart is the honest test -- a save whose value only shows while the same process still holds
# the tokens proves nothing about resuming an agent.
set -uo pipefail
ARM="$1"; EXTRA="${2:-}"
LS=/Applications/Ollama.app/Contents/Resources/llama-server
BLOB=~/.ollama/models/blobs/sha256-7cd4618c1faf8b7233c6c906dac1694b6a47684b37b8895d470ac688520b9c01
OUT=/tmp/swaprobe
PORT=8099

start() {
  lsof -tnP -iTCP:$PORT -sTCP:LISTEN | xargs -r kill 2>/dev/null
  sleep 1
  screen -dmS swa zsh -c "$LS -m $BLOB -c 32768 --parallel 1 --port $PORT --host 127.0.0.1 -ngl 99 -lv 5 ${=EXTRA} --slot-save-path $OUT > $OUT/$ARM-$1.log 2>&1"
  for i in {1..40}; do
    curl -s -m 2 http://127.0.0.1:$PORT/health >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "server did not come up"; return 1
}

start first || exit 1
python3 - "$ARM" << 'PY_EOF'
import json, sys, time, urllib.request
ARM = sys.argv[1]
PROMPT = ("Here is a fragment of a configuration file.\n" +
          "\n".join("key_%04d = value_%04d  # comment %d" % (i, i, i) for i in range(1200)) +
          "\nWhat is the value of key_0500?")
def post(path, body=None):
    req = urllib.request.Request("http://127.0.0.1:8099%s" % path,
                                 data=json.dumps(body or {}).encode(),
                                 headers={"content-type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read()), time.time() - t
cold, t_cold = post("/completion", {"prompt": PROMPT, "n_predict": 1, "cache_prompt": True})
saved, t_save = post("/slots/0?action=save", {"filename": "%s.bin" % ARM})
json.dump({"prompt_tokens": cold.get("tokens_evaluated"), "cold_s": round(t_cold, 2),
           "save": saved, "save_s": round(t_save, 3)}, open("/tmp/swaprobe/%s-before.json" % ARM, "w"), indent=1)
print("prefill %.2fs, saved %s (%s bytes)" % (t_cold, saved.get("n_saved"), saved.get("n_written")))
PY_EOF

start second || exit 1
python3 - "$ARM" << 'PY_EOF'
import json, sys, time, urllib.request
ARM = sys.argv[1]
PROMPT = ("Here is a fragment of a configuration file.\n" +
          "\n".join("key_%04d = value_%04d  # comment %d" % (i, i, i) for i in range(1200)) +
          "\nWhat is the value of key_0500?")
def post(path, body=None):
    req = urllib.request.Request("http://127.0.0.1:8099%s" % path,
                                 data=json.dumps(body or {}).encode(),
                                 headers={"content-type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read()), time.time() - t
try:
    restored, t_restore = post("/slots/0?action=restore", {"filename": "%s.bin" % ARM})
except Exception as exc:
    restored, t_restore = {"error": str(exc)}, 0.0
after, t_after = post("/completion", {"prompt": PROMPT, "n_predict": 1, "cache_prompt": True})
out = {"restore": restored, "restore_s": round(t_restore, 3),
       "after_s": round(t_after, 2), "after_evaluated": after.get("tokens_evaluated")}
json.dump(out, open("/tmp/swaprobe/%s-after.json" % ARM, "w"), indent=1)
print("restore %.3fs -> %s ; answer %.2fs re-evaluating %s tokens"
      % (t_restore, restored.get("n_restored"), t_after, after.get("tokens_evaluated")))
PY_EOF
