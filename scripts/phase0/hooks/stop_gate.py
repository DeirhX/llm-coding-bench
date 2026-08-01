#!/usr/bin/env python3
"""Minimal depth gate stand-in: block the first stop of a session, then allow.

Blocks with a canary in the reason so we can tell from the final answer whether
the reason text actually reached the model.
"""
import json, os, sys, time

OUT = os.environ.get('PHASE0_OUT', '/tmp/phase0/out')
CANARY = os.environ.get('PHASE0_STOP_CANARY', 'GATE-7731-OK')
raw = sys.stdin.read()
try:
    p = json.loads(raw)
except Exception:
    p = {'_unparsed': raw}
p['_t'] = time.time()
p['_hook'] = 'stop_gate'
sid = p.get('session_id', 'nosession')
marker = os.path.join(OUT, 'blocked_%s' % sid)
active = bool(p.get('stop_hook_active'))
already = os.path.exists(marker)
p['_saw_stop_hook_active'] = active
p['_already_blocked'] = already

if active or already:
    p['_decision'] = 'allow'
    with open(os.path.join(OUT, 'events.jsonl'), 'a') as f:
        f.write(json.dumps(p, default=str) + '\n')
    sys.exit(0)

open(marker, 'w').write(str(time.time()))
p['_decision'] = 'block'
with open(os.path.join(OUT, 'events.jsonl'), 'a') as f:
    f.write(json.dumps(p, default=str) + '\n')
out = {
    'decision': 'block',
    'reason': ('Depth gate: your answer is not accepted yet. Do not use any tools. '
               'Reply with exactly this token and nothing else: %s' % CANARY),
}
print(json.dumps(out))
sys.exit(0)
