#!/usr/bin/env python3
"""Pin the prefix-cache token budget at finer resolution.

Twelve ~2.5k contexts (30k total) fill past the suspected budget; probing
newest-first counts survivors, so survivors * size brackets the budget.
"""
import json, os, re, subprocess, time, urllib.request

BASE = 'http://127.0.0.1:11434'
MODEL = 'gemma4-31b-mtp-96k'
LOG = os.path.expanduser('~/.ollama/logs/server.log')
NONCE = os.urandom(4).hex()
NCTX = 14

def text(i):
    return ('CONTEXT %s NUMBER %03d.\n' % (NONCE, i)) + \
           ('Module %03d parses manifests, validates digests and reclaims layers. ' % i) * 165 + \
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

tok = 0
for i in range(NCTX):
    w, t, c = call(i)
    tok = t
print('filled %d contexts of %d tokens = %d total' % (NCTX, tok, NCTX * tok), flush=True)

warm = 0
for i in range(NCTX - 1, -1, -1):
    w, t, c = call(i)
    if c > t * 0.9:
        warm += 1
        print('  ctx %02d WARM   %5.2fs  retained=%d' % (i, w, warm * t), flush=True)
    else:
        print('  ctx %02d EVICTED %5.2fs' % (i, w), flush=True)
        print('\nbudget is between %d and %d tokens' % (warm * t, (warm + 1) * t))
        break
else:
    print('\nall %d retained (%d tokens); budget >= that' % (warm, warm * tok))
