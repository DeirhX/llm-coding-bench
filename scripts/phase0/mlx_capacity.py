#!/usr/bin/env python3
"""Two large contexts that together exceed the KV budget: does parking still work?"""
import json, time, urllib.request

BASE = 'http://127.0.0.1:11434'
MODEL = 'gemma4-31b-mtp-96k'

# ~60k tokens each: well under the 98304 budget alone, over it together.
A = 'REPO NOTE ALPHA.\n' + ('The alpha subsystem parses manifests, validates digests and reconciles layers. ' * 4200)
B = 'REPO NOTE BETA.\n' + ('The beta subsystem schedules runners, reclaims memory and drains queues. ' * 4400)

def ask(text, label):
    body = {'model': MODEL, 'messages': [{'role': 'user', 'content': text + '\n\nReply with one word: OK'}],
            'stream': False, 'options': {'num_predict': 2, 'temperature': 0}, 'keep_alive': '8h'}
    req = urllib.request.Request(BASE + '/api/chat', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=3600) as r:
        o = json.loads(r.read())
    print('%-18s wall=%7.1fs prompt_eval_count=%-7s prompt_eval_ms=%.0f' % (
        label, time.time() - t0, o.get('prompt_eval_count'), o.get('prompt_eval_duration', 0) / 1e6),
        flush=True)

ask(A, '1 big A cold')
ask(B, '2 big B cold')
ask(A, '3 big A again')
