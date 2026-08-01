#!/usr/bin/env python3
"""The Claude Code fan-out pattern, reproduced at real sizes without Claude Code.

An 18k "parent" alternating with 9.7k "workers" that share a byte-identical head,
to see whether sibling reuse survives interleaving and whether the cache wipe is
Ollama's or the client's.
"""
import json, os, re, subprocess, time, urllib.request

BASE = 'http://127.0.0.1:11434'
MODEL = 'gemma4-31b-mtp-96k'
LOG = os.path.expanduser('~/.ollama/logs/server.log')
N = os.urandom(4).hex()
WHEAD = 'WORKER SYSTEM PROMPT %s.\n' % N + 'The worker inspects manifests and validates digests. ' * 970
PARENT = 'PARENT SYSTEM PROMPT %s.\n' % N + 'The supervisor plans work and reviews reports. ' * 1900

def ask(text, label):
    body = {'model': MODEL, 'messages': [{'role': 'user', 'content': text}],
            'stream': False, 'options': {'num_predict': 2, 'temperature': 0}, 'keep_alive': '8h'}
    req = urllib.request.Request(BASE + '/api/chat', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        json.loads(r.read())
    w = time.time() - t0
    out = subprocess.run(['tail', '-300', LOG], capture_output=True, text=True).stdout
    wipes = out.count('freeing all caches')
    t, m, c = re.findall(r'msg="cache (?:hit|miss)" total=(\d+) matched=(\d+) cached=(\d+)', out)[-1]
    print('%-30s wall=%6.2fs total=%-6s matched=%-6s cached=%-6s %s' % (
        label, w, t, m, c, 'REUSED' if int(c) > 1000 else 'COLD'), flush=True)
    return wipes

base = ask(PARENT + '\nPlan the work. Reply OK.', 'parent cold')
for i, job in enumerate(['alpha', 'beta', 'gamma', 'delta', 'epsilon'], 1):
    ask(WHEAD + '\nJOB: inspect %s. Reply OK.' % job, 'worker %d (%s)' % (i, job))
    ask(PARENT + '\nPlan the work. Reply OK. Turn %d.' % i, '  parent resume %d' % i)
out = subprocess.run(['tail', '-400', LOG], capture_output=True, text=True).stdout
print('\ncache wipes during this run:', out.count('freeing all caches') - base)
