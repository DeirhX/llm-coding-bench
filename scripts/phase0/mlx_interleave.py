#!/usr/bin/env python3
"""A -> A -> B -> A against the resident MLX runner, to see if a parked context survives."""
import json, time, urllib.request

BASE = 'http://127.0.0.1:11434'
MODEL = 'gemma4-31b-mtp-96k'

A = 'Here is repository note ALPHA.\n' + ('The alpha subsystem parses manifests and validates digests. ' * 260)
B = 'Here is repository note BETA.\n' + ('The beta subsystem schedules runners and reclaims memory. ' * 260)

def ask(text, label):
    body = {'model': MODEL, 'messages': [{'role': 'user', 'content': text + '\n\nReply with one word: OK'}],
            'stream': False, 'options': {'num_predict': 2, 'temperature': 0}, 'keep_alive': '8h'}
    req = urllib.request.Request(BASE + '/api/chat', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        o = json.loads(r.read())
    print('%-22s wall=%6.1fs prompt_eval_count=%-6s prompt_eval_ms=%-7.0f eval_count=%s' % (
        label, time.time() - t0, o.get('prompt_eval_count'),
        o.get('prompt_eval_duration', 0) / 1e6, o.get('eval_count')))

ask(A, '1 A cold')
ask(A, '2 A repeat')
ask(B, '3 B cold')
ask(A, '4 A after B')
