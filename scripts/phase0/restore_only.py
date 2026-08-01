#!/usr/bin/env python3
"""Restore a saved slot into a freshly started server, then check prefill is skipped."""
import json, time, urllib.request

BASE = 'http://127.0.0.1:8099'
BIG = ('The quick brown fox jumps over the lazy dog. ' * 400) + '\nSummary:'

def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read()), time.time() - t0

print('--- restore into empty slot ---')
o, w = post('/slots/0?action=restore', {'filename': 'probeA.bin'})
print('wall=%.3fs %s' % (w, json.dumps(o)))

print('--- prompt A (identical to the saved one) ---')
o, w = post('/completion', {'prompt': BIG, 'n_predict': 8, 'cache_prompt': True, 'temperature': 0})
t = o['timings']
print('wall=%.2fs prompt_n=%s prompt_ms=%.0f' % (w, t['prompt_n'], t['prompt_ms']))

print('--- prompt A once more (RAM cache path, for contrast) ---')
o, w = post('/completion', {'prompt': BIG, 'n_predict': 8, 'cache_prompt': True, 'temperature': 0})
t = o['timings']
print('wall=%.2fs prompt_n=%s prompt_ms=%.0f' % (w, t['prompt_n'], t['prompt_ms']))
