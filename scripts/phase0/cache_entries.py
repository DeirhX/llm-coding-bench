#!/usr/bin/env python3
"""How many distinct contexts keep reusable KV, not merely a matchable token list?

Cycles N small contexts twice and reports matched vs cached for each request.
"""
import json, time, urllib.request

BASE = 'http://127.0.0.1:11434'
MODEL = 'gemma4-31b-mtp-96k'
NAMES = ['ALPHA', 'BETA', 'GAMMA', 'DELTA']
CTX = {n: ('CONTEXT %s.\n' % n) + ('The %s module handles requests and returns results. ' % n) * 260
       for n in NAMES}

def ask(name):
    body = {'model': MODEL,
            'messages': [{'role': 'user', 'content': CTX[name] + '\n\nReply with one word: OK'}],
            'stream': False, 'options': {'num_predict': 2, 'temperature': 0}, 'keep_alive': '8h'}
    req = urllib.request.Request(BASE + '/api/chat', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        json.loads(r.read())
    return time.time() - t0

order = NAMES + NAMES + NAMES[:2]
for i, n in enumerate(order):
    w = ask(n)
    print('%2d  %-6s wall=%5.2fs' % (i + 1, n, w), flush=True)
