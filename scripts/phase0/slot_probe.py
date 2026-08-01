#!/usr/bin/env python3
"""Measure whether a saved slot restore actually skips prefill."""
import json, time, urllib.request

BASE = 'http://127.0.0.1:8099'

def post(path, body):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.loads(r.read())
    return out, time.time() - t0

# A prompt long enough that prefill is measurable and cache reuse is obvious.
BIG = ('The quick brown fox jumps over the lazy dog. ' * 400) + '\nSummary:'

def completion(prompt, n=8):
    return post('/completion', {'prompt': prompt, 'n_predict': n, 'cache_prompt': True,
                                'temperature': 0})

def timings(o):
    t = o.get('timings', {})
    return 'prompt_n=%s prompt_ms=%.0f predicted_n=%s predicted_ms=%.0f' % (
        t.get('prompt_n'), t.get('prompt_ms', 0), t.get('predicted_n'), t.get('predicted_ms', 0))

print('--- 1. cold prefill of prompt A ---')
o, w = completion(BIG)
print('wall=%.2fs %s' % (w, timings(o)))

print('--- 2. save slot 0 ---')
o, w = post('/slots/0?action=save', {'filename': 'probeA.bin'})
print('wall=%.2fs %s' % (w, json.dumps(o)))

print('--- 3. overwrite slot with prompt B ---')
o, w = completion('Completely different text about marine biology. ' * 400 + '\nSummary:')
print('wall=%.2fs %s' % (w, timings(o)))

print('--- 4. prompt A again WITHOUT restore (should re-prefill) ---')
o, w = completion(BIG)
print('wall=%.2fs %s' % (w, timings(o)))

print('--- 5. overwrite slot with prompt B again ---')
o, w = completion('Completely different text about marine biology. ' * 400 + '\nSummary:')
print('wall=%.2fs %s' % (w, timings(o)))

print('--- 6. restore slot A from disk ---')
o, w = post('/slots/0?action=restore', {'filename': 'probeA.bin'})
print('wall=%.2fs %s' % (w, json.dumps(o)))

print('--- 7. prompt A after restore (prefill should be skipped) ---')
o, w = completion(BIG)
print('wall=%.2fs %s' % (w, timings(o)))
