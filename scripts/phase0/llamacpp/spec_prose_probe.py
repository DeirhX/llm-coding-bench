"""The control the earlier 1B probe got wrong: speculation on output that does not echo the input.

`ngram-cache` was a 1.7x *slowdown* on prose. If ngram-mod costs nothing here, it can be left on
permanently; if it costs, it has to be switched per task, which is a different piece of plumbing.
"""
import json, sys, time, urllib.request
ARM = sys.argv[1]
PROMPT = ("<start_of_turn>user\nExplain, in about 600 words of continuous prose with no code and no "
          "lists, why a reviewer should distrust a code review that cites no line numbers.\n"
          "<end_of_turn>\n<start_of_turn>model\n")
req = urllib.request.Request("http://127.0.0.1:8098/completion",
                             data=json.dumps({"prompt": PROMPT, "n_predict": 800,
                                              "temperature": 0.0, "cache_prompt": False}).encode(),
                             headers={"content-type": "application/json"})
started = time.time()
with urllib.request.urlopen(req, timeout=1800) as r:
    out = json.loads(r.read())
t = out.get("timings", {})
result = {"arm": ARM, "task": "prose", "wall_s": round(time.time() - started, 2),
          "predicted_tokens": t.get("predicted_n"),
          "decode_tok_s": round(t.get("predicted_per_second") or 0, 2)}
json.dump(result, open("/tmp/ngram/%s-prose.json" % ARM, "w"), indent=1)
open("/tmp/ngram/%s-prose.out" % ARM, "w").write(out.get("content", ""))
print(json.dumps(result))
