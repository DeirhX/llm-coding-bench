#!/usr/bin/env python3
"""Trie-split hypothesis: the first divergence from a shared prefix pays, later ones are free."""
import json, os, re, subprocess, time, urllib.request

BASE = 'http://127.0.0.1:11434'
MODEL = 'gemma4-31b-mtp-96k'
LOG = os.path.expanduser('~/.ollama/logs/server.log')
N = os.urandom(4).hex()
P = 'PREAMBLE %s.\n' % N + 'You are a careful assistant answering in one word. ' * 300
Q = 'OTHER %s.\n' % N + 'The marine subsystem tracks buoys. ' * 200 + ' Reply OK.'

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
    print('%-34s wall=%5.2fs total=%-6s matched=%-6s cached=%-6s %s' % (
        label, w, t, m, c, 'REUSED' if int(c) > 1000 else 'lost'), flush=True)

ask(P + '\nTASK A: name a colour. Reply OK.', 'A  first use of prefix')
ask(Q, 'Q  displace')
ask(P + '\nTASK B: name a fruit. Reply OK.', 'B  1st branch (predict lost)')
ask(Q, 'Q  displace')
ask(P + '\nTASK C: name a city. Reply OK.', 'C  2nd branch (predict REUSED)')
ask(Q, 'Q  displace')
ask(P + '\nTASK D: name a river. Reply OK.', 'D  3rd branch (predict REUSED)')
