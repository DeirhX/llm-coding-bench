#!/usr/bin/env python3
"""Find what actually bounds prefix-cache retention: a token budget, or memory.

Creates N distinct ~8k contexts (total well over the 98,304 window), then probes
every one of them in creation order to see which survived.
"""
import json, os, re, subprocess, time, urllib.request

BASE = 'http://127.0.0.1:11434'
MODEL = 'gemma4-31b-mtp-96k'
LOG = os.path.expanduser('~/.ollama/logs/server.log')
N = os.urandom(4).hex()
NCTX = 14

def body_for(i):
    return ('CONTEXT %s NUMBER %03d.\n' % (N, i)) + \
           ('Module %03d parses manifests, validates digests and reclaims layers. ' % i) * 620

def ask(i, label):
    body = {'model': MODEL, 'messages': [{'role': 'user', 'content': body_for(i) + '\nReply OK.'}],
            'stream': False, 'options': {'num_predict': 2, 'temperature': 0}, 'keep_alive': '8h'}
    req = urllib.request.Request(BASE + '/api/chat', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        json.loads(r.read())
    w = time.time() - t0
    out = subprocess.run(['tail', '-100', LOG], capture_output=True, text=True).stdout
    t, m, c = re.findall(r'msg="cache (?:hit|miss)" total=(\d+) matched=(\d+) cached=(\d+)', out)[-1]
    return w, int(t), int(m), int(c)

def size_gb():
    out = subprocess.run(['ollama', 'ps'], capture_output=True, text=True).stdout
    m = re.search(r'(\d+)\s+GB', out)
    return m.group(1) if m else '?'

total = 0
print('-- filling --  (runner size before: %s GB)' % size_gb())
for i in range(NCTX):
    w, t, m, c = ask(i, 'fill')
    total += t
    print('  ctx %02d  tokens=%-6d cumulative=%-7d wall=%5.1fs  size=%s GB' % (i, t, total, w, size_gb()),
          flush=True)

print('\n-- probing in creation order (warm = survived) --')
survived = []
for i in range(NCTX):
    w, t, m, c = ask(i, 'probe')
    ok = c > t * 0.9
    survived.append(ok)
    print('  ctx %02d  matched=%-6d cached=%-6d wall=%5.1fs  %s' % (i, m, c, w, 'WARM' if ok else 'EVICTED'),
          flush=True)
print('\nsurvivors: %d of %d, cumulative tokens created: %d, window: 98304' % (
    sum(survived), NCTX, total))
