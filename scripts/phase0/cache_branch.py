#!/usr/bin/env python3
"""Does a shared prefix get reused when the tail diverges (the subagent shape)?

P+A, then P+B, then an unrelated Q, then P+C.
"""
import json, time, urllib.request

BASE = 'http://127.0.0.1:11434'
MODEL = 'gemma4-31b-mtp-96k'
P = 'SHARED SYSTEM PREAMBLE.\n' + ('You are a careful assistant that answers in one word. ' * 300)
Q = 'UNRELATED CONTEXT.\n' + ('The marine subsystem tracks buoys and tides. ' * 300)

def ask(text, label):
    body = {'model': MODEL, 'messages': [{'role': 'user', 'content': text}],
            'stream': False, 'options': {'num_predict': 2, 'temperature': 0}, 'keep_alive': '8h'}
    req = urllib.request.Request(BASE + '/api/chat', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        json.loads(r.read())
    print('%-28s wall=%5.2fs' % (label, time.time() - t0), flush=True)

ask(P + '\nTASK A: name a colour. Reply OK.', 'P+A cold')
ask(P + '\nTASK B: name a fruit. Reply OK.', 'P+B (prefix shared)')
ask(Q + '\nTASK Z: name a fish. Reply OK.', 'Q unrelated')
ask(P + '\nTASK C: name a city. Reply OK.', 'P+C (after intervening Q)')
ask(P + '\nTASK A: name a colour. Reply OK.', 'P+A again (exact repeat)')
