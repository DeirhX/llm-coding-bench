#!/usr/bin/env python3
"""Decode-rate comparison on a self-repetitive prompt, run against whatever server is up."""
import json, sys, time, urllib.request

BASE = 'http://127.0.0.1:8099'
PASSAGE = ('Refactor the loader so that the cache key includes the tokenizer revision, '
           'because two checkpoints that share a name can disagree on special tokens. ') * 12
PROMPT = (PASSAGE + '\n\nNow repeat the passage above word for word, exactly, twice:\n' + PASSAGE)

req = urllib.request.Request(BASE + '/completion',
                             data=json.dumps({'prompt': PROMPT, 'n_predict': 256,
                                              'cache_prompt': False, 'temperature': 0}).encode(),
                             headers={'Content-Type': 'application/json'})
t0 = time.time()
with urllib.request.urlopen(req, timeout=600) as r:
    o = json.loads(r.read())
t = o['timings']
print('%s: prompt_n=%s prompt_ms=%.0f predicted_n=%s predicted_ms=%.0f -> %.1f tok/s (wall %.2fs)' % (
    sys.argv[1], t['prompt_n'], t['prompt_ms'], t['predicted_n'], t['predicted_ms'],
    t['predicted_n'] / (t['predicted_ms'] / 1000.0), time.time() - t0))
