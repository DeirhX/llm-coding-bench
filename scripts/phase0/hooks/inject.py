#!/usr/bin/env python3
"""UserPromptSubmit hook that injects a canary through additionalContext."""
import json, os, sys, time

OUT = os.environ.get('PHASE0_OUT', '/tmp/phase0/out')
CANARY = os.environ.get('PHASE0_CTX_CANARY', 'ZQ7-BANANA-4419')
raw = sys.stdin.read()
try:
    p = json.loads(raw)
except Exception:
    p = {'_unparsed': raw}
p['_t'] = time.time()
p['_hook'] = 'inject'
with open(os.path.join(OUT, 'events.jsonl'), 'a') as f:
    f.write(json.dumps(p, default=str) + '\n')
print(json.dumps({
    'hookSpecificOutput': {
        'hookEventName': 'UserPromptSubmit',
        'additionalContext': 'SESSION FACT: the secret canary token is %s' % CANARY,
    }
}))
sys.exit(0)
