#!/usr/bin/env python3
"""Does reuse of a saved entry depend on how far back the divergence point is?

Builds a saved entry of PREFIX+LONG_TAIL, displaces it with an unrelated request,
then asks for PREFIX+DIFFERENT_TAIL, i.e. branching deep inside the saved entry.
"""
import json, os, time, urllib.request

BASE = 'http://127.0.0.1:11434'
MODEL = 'gemma4-31b-mtp-96k'
N = os.urandom(4).hex()
P = 'SHARED PREAMBLE %s.\n' % N + ('You are a careful assistant answering in one word. ' * 300)
LONG_A = '\nTASK A: ' + ('consider the alpha pathway carefully. ' * 300) + ' Reply OK.'
SHORT_C = '\nTASK C: name a city. Reply OK.'
Q = 'UNRELATED %s.\n' % N + ('The marine subsystem tracks buoys. ' * 300) + ' Reply OK.'

def ask(text, label):
    body = {'model': MODEL, 'messages': [{'role': 'user', 'content': text}],
            'stream': False, 'options': {'num_predict': 2, 'temperature': 0}, 'keep_alive': '8h'}
    req = urllib.request.Request(BASE + '/api/chat', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        json.loads(r.read())
    print('%-42s wall=%5.2fs' % (label, time.time() - t0), flush=True)

ask(P + LONG_A, 'P+LONG_A cold (saved entry ~5.7k)')
ask(Q, 'Q unrelated (displaces the active slot)')
ask(P + SHORT_C, 'P+SHORT_C: branch ~2.4k back into entry')
ask(Q, 'Q again')
ask(P + SHORT_C, 'P+SHORT_C repeat (now its own entry)')
