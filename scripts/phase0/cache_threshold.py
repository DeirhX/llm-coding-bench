#!/usr/bin/env python3
"""How many tokens of divergence a saved entry tolerates before reuse is abandoned."""
import json, os, re, subprocess, time, urllib.request

BASE = 'http://127.0.0.1:11434'
MODEL = 'gemma4-31b-mtp-96k'
LOG = os.path.expanduser('~/.ollama/logs/server.log')
WORD = 'token '          # ~1 token each
PRE = 'You are a careful assistant answering in one word. '

def ask(text):
    body = {'model': MODEL, 'messages': [{'role': 'user', 'content': text}],
            'stream': False, 'options': {'num_predict': 2, 'temperature': 0}, 'keep_alive': '8h'}
    req = urllib.request.Request(BASE + '/api/chat', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=1800) as r:
        json.loads(r.read())

def tail_lines(n):
    out = subprocess.run(['tail', '-400', LOG], capture_output=True, text=True).stdout
    hits = re.findall(r'msg="cache (?:hit|miss)" total=(\d+) matched=(\d+) cached=(\d+)', out)
    return hits[-n:]

print('%8s %10s %10s %10s' % ('divergeN', 'total', 'matched', 'cached'))
for depth in (32, 128, 512, 1024, 2048, 4096):
    n = os.urandom(4).hex()
    P = 'PREAMBLE %s.\n' % n + PRE * 300
    entry = P + '\nTAIL ' + WORD * depth + ' Reply OK.'
    other = 'OTHER %s.\n' % n + 'The marine subsystem tracks buoys. ' * 200 + ' Reply OK.'
    ask(entry)          # create the saved entry
    ask(other)          # displace it so it is no longer the active slot
    ask(P + '\nZ. Reply OK.')   # branch at |P|, truncating ~depth tokens
    t, m, c = tail_lines(1)[0]
    print('%8d %10s %10s %10s  %s' % (depth, t, m, c, 'REUSED' if int(c) > 1000 else 'lost'))
