#!/usr/bin/env python3
"""Operating rules for a sibling fan-out sharing one prompt head.

Phase 1 primes the shared node. Phase 2 runs siblings back to back. Phase 3 tests
whether a tiny throwaway request is enough to displace the active slot.
"""
import json, os, re, subprocess, time, urllib.request

BASE = 'http://127.0.0.1:11434'
MODEL = 'gemma4-31b-mtp-96k'
LOG = os.path.expanduser('~/.ollama/logs/server.log')
N = os.urandom(4).hex()
HEAD = 'WORKER PREAMBLE %s.\n' % N + 'You are a careful worker answering in one word. ' * 300
BIGQ = 'OTHER %s.\n' % N + 'The marine subsystem tracks buoys. ' * 200 + ' Reply OK.'
TINY = 'Reply OK. %s' % N

def ask(text, label):
    body = {'model': MODEL, 'messages': [{'role': 'user', 'content': text}],
            'stream': False, 'options': {'num_predict': 2, 'temperature': 0}, 'keep_alive': '8h'}
    req = urllib.request.Request(BASE + '/api/chat', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        json.loads(r.read())
    w = time.time() - t0
    out = subprocess.run(['tail', '-200', LOG], capture_output=True, text=True).stdout
    t, m, c = re.findall(r'msg="cache (?:hit|miss)" total=(\d+) matched=(\d+) cached=(\d+)', out)[-1]
    print('%-40s wall=%5.2fs total=%-6s matched=%-6s cached=%-6s %s' % (
        label, w, t, m, c, 'REUSED' if int(c) > 1000 else 'lost'), flush=True)

print('-- phase 1: prime the shared node --')
ask(HEAD + '\nJOB A: name a colour. Reply OK.', 'A first use')
ask(BIGQ, 'displace')
ask(HEAD + '\nJOB B: name a fruit. Reply OK.', 'B first branch (pays)')
ask(BIGQ, 'displace')

print('-- phase 2: siblings back to back, no displacer --')
for job in ('C: name a city', 'D: name a river', 'E: name a tree', 'F: name a metal'):
    ask(HEAD + '\nJOB %s. Reply OK.' % job, 'sibling %s' % job.split(':')[0])

print('-- phase 3: tiny displacer between siblings --')
for job in ('G: name a bird', 'H: name a fish'):
    ask(TINY, '  tiny displacer')
    ask(HEAD + '\nJOB %s. Reply OK.' % job, 'sibling %s (after tiny)' % job.split(':')[0])
