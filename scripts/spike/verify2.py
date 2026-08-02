#!/usr/bin/env python3
"""Classify each failing citation: wrong content, wrong lines, or wrong indentation."""
import glob, json, os, re, textwrap

ROOT = '/Users/deirh/Projects/llm-coding-bench'
stop = json.load(open(sorted(glob.glob('/tmp/spike/out/stop-*.json'))[0]))
events = [json.loads(l) for l in open(os.path.expanduser(stop['payload']['transcript_path'])) if l.strip()]
body = []
for e in events:
    m = e.get('message') or {}
    if m.get('role') != 'assistant':
        continue
    c = m.get('content')
    body += [c] if isinstance(c, str) else [x['text'] for x in c if isinstance(x, dict) and x.get('type') == 'text']
text = '\n'.join(body)

def strip_blank(s):
    return [l for l in s.strip('\n').split('\n') if l.strip()]

for i, b in enumerate(re.split(r'^CLAIM:', text, flags=re.M)[1:], 1):
    m = re.search(r'^EVIDENCE:\s*(\S+?):(\d+)-(\d+)\s*$', b, re.M)
    q = re.search(r'^QUOTE:\s*\n(.*?)(?=\n\s*\n(?:CLAIM:|UNKNOWN:)|\Z)', b, re.M | re.S)
    if not (m and q):
        continue
    path, a, z = m.group(1), int(m.group(2)), int(m.group(3))
    lines = open(os.path.join(ROOT, path)).read().split('\n')
    cited = strip_blank('\n'.join(lines[a - 1:z]))
    quoted = strip_blank(q.group(1))
    exact = cited == quoted
    dedented = textwrap.dedent('\n'.join(cited)).split('\n') == textwrap.dedent('\n'.join(quoted)).split('\n')
    if exact:
        print('%d. EXACT            %s:%d-%d' % (i, path, a, z))
    elif dedented:
        shift = (len(quoted[0]) - len(quoted[0].lstrip())) - (len(cited[0]) - len(cited[0].lstrip()))
        print('%d. CONTENT OK, INDENT WRONG by %+d spaces   %s:%d-%d' % (i, shift, path, a, z))
    else:
        print('%d. CONTENT WRONG    %s:%d-%d' % (i, path, a, z))
        for cl, ql in zip(cited, quoted):
            if cl != ql:
                print('      file  : %r' % cl[:76])
                print('      quote : %r' % ql[:76])
                break
        if len(cited) != len(quoted):
            print('      line count: file %d, quote %d' % (len(cited), len(quoted)))
