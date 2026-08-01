#!/usr/bin/env python3
"""Block N times in a row, ignoring stop_hook_active, to find the client's cap."""
import json, os, sys, time

OUT = os.environ.get('PHASE0_OUT', '/tmp/phase0/out')
MAX = int(os.environ.get('PHASE0_MAX_BLOCKS', '3'))
raw = sys.stdin.read()
try:
    p = json.loads(raw)
except Exception:
    p = {'_unparsed': raw}
sid = p.get('session_id', 'nosession')
counter = os.path.join(OUT, 'count_%s' % sid)
n = int(open(counter).read()) if os.path.exists(counter) else 0
p['_hook'] = 'stop_multi'
p['_t'] = time.time()
p['_block_index'] = n
p['_saw_stop_hook_active'] = bool(p.get('stop_hook_active'))
p['_decision'] = 'block' if n < MAX else 'allow'
open(counter, 'w').write(str(n + 1))
with open(os.path.join(OUT, 'events.jsonl'), 'a') as f:
    f.write(json.dumps(p, default=str) + '\n')
if n < MAX:
    print(json.dumps({
        'decision': 'block',
        'reason': 'Round %d of %d. Do not use tools. Reply with exactly: ROUND-%d' % (n + 1, MAX, n + 1),
    }))
sys.exit(0)
