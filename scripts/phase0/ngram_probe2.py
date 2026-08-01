#!/usr/bin/env python3
"""Decode rate on freeform, non-repetitive output: the pessimistic case for ngram speculation."""
import json, sys, time, urllib.request

BASE = 'http://127.0.0.1:8099'
PROMPT = 'Write a detailed original short story about a lighthouse keeper who collects clocks.\n\n'
req = urllib.request.Request(BASE + '/completion',
                             data=json.dumps({'prompt': PROMPT, 'n_predict': 256,
                                              'cache_prompt': False, 'temperature': 0}).encode(),
                             headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req, timeout=600) as r:
    o = json.loads(r.read())
t = o['timings']
print('%-14s predicted_n=%s predicted_ms=%.0f -> %.1f tok/s' % (
    sys.argv[1], t['predicted_n'], t['predicted_ms'], t['predicted_n'] / (t['predicted_ms'] / 1000.0)))
