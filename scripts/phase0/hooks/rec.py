#!/usr/bin/env python3
"""Record a hook payload verbatim and get out of the way."""
import json, os, sys, time

OUT = os.environ.get('PHASE0_OUT', '/tmp/phase0/out')
raw = sys.stdin.read()
try:
    payload = json.loads(raw)
except Exception:
    payload = {'_unparsed': raw}
payload['_t'] = time.time()
ev = payload.get('hook_event_name', 'unknown')
with open(os.path.join(OUT, 'events.jsonl'), 'a') as f:
    f.write(json.dumps(payload, default=str) + '\n')
print('', end='')
sys.exit(0)
