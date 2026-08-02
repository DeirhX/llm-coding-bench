#!/usr/bin/env python3
"""file_quote verifier, first cut: does each cited range actually contain the quote?"""
import glob, json, os, re, sys

ROOT = '/Users/deirh/Projects/llm-coding-bench'
OUT = '/tmp/spike/out'

stop = json.load(open(sorted(glob.glob(os.path.join(OUT, 'stop-*.json')))[0]))
events = [json.loads(l) for l in open(os.path.expanduser(stop['payload']['transcript_path'])) if l.strip()]
body = []
for e in events:
    msg = e.get('message') or {}
    if msg.get('role') != 'assistant':
        continue
    c = msg.get('content')
    if isinstance(c, str):
        body.append(c)
    elif isinstance(c, list):
        body += [x['text'] for x in c if isinstance(x, dict) and x.get('type') == 'text']
text = '\n'.join(body)

blocks = re.split(r'^CLAIM:', text, flags=re.M)[1:]
print('%d claims to verify\n' % len(blocks))

def norm(s):
    return [ln.rstrip() for ln in s.strip('\n').split('\n') if ln.strip()]

passed = failed = 0
for i, b in enumerate(blocks, 1):
    claim = b.strip().split('\n')[0].strip()
    m = re.search(r'^EVIDENCE:\s*(\S+?):(\d+)-(\d+)\s*$', b, re.M)
    q = re.search(r'^QUOTE:\s*\n(.*?)(?=\n\s*\n(?:CLAIM:|UNKNOWN:)|\Z)', b, re.M | re.S)
    if not m or not q:
        print('%d. UNPARSEABLE  %s' % (i, claim[:70])); failed += 1; continue
    path, a, z = m.group(1), int(m.group(2)), int(m.group(3))
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        print('%d. FAIL no such file %s' % (i, path)); failed += 1; continue
    lines = open(full).read().split('\n')
    cited = norm('\n'.join(lines[a - 1:z]))
    quoted = norm(q.group(1))
    if cited == quoted:
        print('%d. PASS  %s:%d-%d' % (i, path, a, z)); passed += 1
        continue
    # is the quote real but mis-cited?
    hay = '\n'.join(norm('\n'.join(lines)))
    where = hay.find('\n'.join(quoted))
    if where >= 0:
        line_of = hay[:where].count('\n') + 1
        print('%d. FAIL wrong line numbers: cited %s:%d-%d, text actually at ~line %d'
              % (i, path, a, z, line_of))
    else:
        print('%d. FAIL quote not present in %s at all' % (i, path))
        for ln in quoted[:3]:
            print('      quoted : %s' % ln[:88])
        for ln in cited[:3]:
            print('      cited  : %s' % ln[:88])
    failed += 1

print('\n%d passed, %d failed' % (passed, failed))
