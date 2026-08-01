#!/usr/bin/env python3
"""Is prefix-cache retention bounded by the runner's token window?

Fills the runner with N distinct ~9.3k contexts (total > 98,304), then probes
newest-first, which is the LRU-friendly order, and stops at the first eviction.
The count of survivors times their size is the effective retention budget.
"""
import json, os, re, subprocess, time, urllib.request

BASE = 'http://127.0.0.1:11434'
MODEL = 'gemma4-31b-mtp-96k'
LOG = os.path.expanduser('~/.ollama/logs/server.log')
NONCE = os.urandom(4).hex()
NCTX = 12

def text(i):
    return ('CONTEXT %s NUMBER %03d.\n' % (NONCE, i)) + \
           ('Module %03d parses manifests, validates digests and reclaims layers. ' % i) * 620 + \
           '\nReply OK.'

def call(i):
    body = {'model': MODEL, 'messages': [{'role': 'user', 'content': text(i)}],
            'stream': False, 'options': {'num_predict': 2, 'temperature': 0}, 'keep_alive': '8h'}
    req = urllib.request.Request(BASE + '/api/chat', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        json.loads(r.read())
    w = time.time() - t0
    out = subprocess.run(['tail', '-100', LOG], capture_output=True, text=True).stdout
    t, m, c = re.findall(r'msg="cache (?:hit|miss)" total=(\d+) matched=(\d+) cached=(\d+)', out)[-1]
    return w, int(t), int(c)

def size_gb():
    out = subprocess.run(['ollama', 'ps'], capture_output=True, text=True).stdout
    m = re.search(r'(\d+)\s+GB', out)
    return m.group(1) if m else '?'

print('filling %d contexts (runner %s GB)' % (NCTX, size_gb()), flush=True)
tok = 0
for i in range(NCTX):
    w, t, c = call(i)
    tok = t
    print('  fill %02d  %5.1fs  cumulative=%d  %s GB' % (i, w, t * (i + 1), size_gb()), flush=True)

print('\nprobing newest-first', flush=True)
warm = 0
for i in range(NCTX - 1, -1, -1):
    w, t, c = call(i)
    ok = c > t * 0.9
    if ok:
        warm += 1
        print('  ctx %02d  WARM     cached=%-6d %5.2fs   retained=%d tokens' % (i, c, w, warm * t), flush=True)
    else:
        print('  ctx %02d  EVICTED  cached=%-6d %5.2fs' % (i, c, w), flush=True)
        print('\nretained %d contexts x %d tokens = %d  (runner window 98304)' % (warm, t, warm * t))
        break
else:
    print('\nall %d retained = %d tokens (window 98304)' % (warm, warm * tok))
