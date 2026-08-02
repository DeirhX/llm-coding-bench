"""Ask for an edited copy of a real source file and record how fast the tokens came out."""
import json, sys, time, urllib.request

ARM = sys.argv[1]
SRC = open("/Users/deirh/Projects/llm-coding-bench/scripts/cc_verify.py").read().split("\n")
BODY = "\n".join(SRC[100:220])
PROMPT = ("<start_of_turn>user\nHere is a Python file.\n\n```python\n%s\n```\n\n"
          "Rewrite it verbatim with exactly one change: rename the function `file_quote` to "
          "`quote_matches_file` everywhere it appears. Output only the complete rewritten code in "
          "one fenced block, no commentary.<end_of_turn>\n<start_of_turn>model\n" % BODY)

req = urllib.request.Request("http://127.0.0.1:8098/completion",
                             data=json.dumps({"prompt": PROMPT, "n_predict": 1400,
                                              "temperature": 0.0, "cache_prompt": False}).encode(),
                             headers={"content-type": "application/json"})
started = time.time()
with urllib.request.urlopen(req, timeout=1800) as r:
    out = json.loads(r.read())
wall = time.time() - started
t = out.get("timings", {})
result = {
    "arm": ARM, "wall_s": round(wall, 2),
    "prompt_tokens": t.get("prompt_n"), "predicted_tokens": t.get("predicted_n"),
    "prefill_tok_s": round(t.get("prompt_per_second") or 0, 1),
    "decode_tok_s": round(t.get("predicted_per_second") or 0, 2),
    "draft_accepted": out.get("draft_n_accepted"), "draft_total": out.get("draft_n"),
    "chars_out": len(out.get("content", "")),
}
json.dump(result, open("/tmp/ngram/%s.json" % ARM, "w"), indent=1)
open("/tmp/ngram/%s.out" % ARM, "w").write(out.get("content", ""))
print(json.dumps(result))
