#!/usr/bin/env python3
"""What triggers 'failed to restore cache, freeing all caches'?

Two suspects, tested against the resident 31B:
  1. an aborted request (client disconnects mid-prefill), which the runner's
     own comments describe as leaving snapshots scheduled but unattached;
  2. two overlapping requests against an -np 1 runner.
Each phase ends by returning to a context that was warm beforehand.
"""
import json, os, re, socket, subprocess, threading, time, urllib.request

BASE = 'http://127.0.0.1:11434'
MODEL = 'gemma4-31b-mtp-96k'
LOG = os.path.expanduser('~/.ollama/logs/server.log')
NONCE = os.urandom(4).hex()

def text(i, n=620):
    return ('CONTEXT %s NUMBER %03d.\n' % (NONCE, i)) + \
           ('Module %03d parses manifests, validates digests and reclaims layers. ' % i) * n + \
           '\nReply OK.'

def wipes():
    out = subprocess.run(['rg', '-c', 'freeing all caches', LOG], capture_output=True, text=True).stdout
    return int(out.strip() or 0)

def call(i, timeout=1800, n=620):
    body = {'model': MODEL, 'messages': [{'role': 'user', 'content': text(i, n)}],
            'stream': False, 'options': {'num_predict': 2, 'temperature': 0}, 'keep_alive': '8h'}
    req = urllib.request.Request(BASE + '/api/chat', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
    except (socket.timeout, TimeoutError, urllib.error.URLError) as e:
        return time.time() - t0, 'ABORTED'
    return time.time() - t0, 'ok'

def state(label, i):
    w, _ = call(i)
    out = subprocess.run(['tail', '-40', LOG], capture_output=True, text=True).stdout
    t, m, c = re.findall(r'msg="cache (?:hit|miss)" total=(\d+) matched=(\d+) cached=(\d+)', out)[-1]
    print('  %-22s cached=%-6s of %-6s  %5.2fs  %s' % (
        label, c, t, w, 'WARM' if int(c) > int(t) * 0.9 else 'COLD'), flush=True)

base = wipes()
print('wipes in log before: %d\n' % base)

print('phase 1: abort a prefill mid-flight')
call(1); state('ctx1 warm again', 1)
w, r = call(2, timeout=4)          # cut the connection ~4 s into a ~15 s prefill
print('  aborted ctx2 after %.1fs -> %s' % (w, r), flush=True)
call(3)                            # force a path switch away from the wreckage
state('ctx1 after abort', 1)
print('  wipes now: %d (+%d)\n' % (wipes(), wipes() - base), flush=True)

mid = wipes()
print('phase 2: two overlapping requests')
call(4); state('ctx4 warm again', 4)
res = {}
def go(i): res[i] = call(i)
ts = [threading.Thread(target=go, args=(x,)) for x in (5, 6)]
[t.start() for t in ts]; [t.join() for t in ts]
print('  concurrent 5/6: %s' % res, flush=True)
state('ctx4 after overlap', 4)
print('  wipes now: %d (+%d)' % (wipes(), wipes() - mid), flush=True)
