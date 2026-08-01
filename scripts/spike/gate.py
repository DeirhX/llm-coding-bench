#!/usr/bin/env python3
"""Stop-hook depth gate, spike version: one hardcoded refusal, then release.

Records every Stop payload so the round boundaries can be timed afterwards.
"""
import json
import os
import sys
import time

OUT = os.environ.get('SPIKE_OUT', '/tmp/spike/out')
MAX_BLOCKS = int(os.environ.get('SPIKE_MAX_BLOCKS', '1'))

REASON = """STOP GATE: this answer is not accepted, because not one of its claims is backed by \
evidence anyone could check.

Rework it now. Use the Read tool on the actual files. Do not answer from memory, and do not \
re-state what you already said.

Your final answer must consist only of blocks in exactly this shape:

CLAIM: <one sentence>
EVIDENCE: <path>:<first_line>-<last_line>
QUOTE:
<the exact lines, copied verbatim from the file you just read>

Repeat the block for each claim. Anything you could not establish goes at the end as:

UNKNOWN: <one sentence>

A claim with no EVIDENCE block, or a QUOTE that is not a verbatim copy of the cited lines, \
counts as a failure."""

def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        print(json.dumps({'systemMessage': 'gate: unreadable payload %s' % exc}))
        return 0

    session = payload.get('session_id', 'nosession')
    counter = os.path.join(OUT, 'blocks-%s.txt' % session)
    seen = 0
    if os.path.exists(counter):
        seen = int((open(counter).read().strip() or '0'))

    with open(os.path.join(OUT, 'stop-%s-%02d.json' % (session, seen)), 'w') as fh:
        json.dump({'wall': time.time(), 'payload': payload}, fh, indent=2)

    if seen >= MAX_BLOCKS:
        return 0

    open(counter, 'w').write(str(seen + 1))
    print(json.dumps({'decision': 'block', 'reason': REASON}))
    return 0

sys.exit(main())
