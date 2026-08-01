#!/usr/bin/env python3
"""Read the spike transcript and answer the four questions the spike exists for."""
import glob, json, os, re, datetime

OUT = '/tmp/spike/out'
stops = sorted(glob.glob(os.path.join(OUT, 'stop-*.json')))
print('Stop hook fired %d time(s)' % len(stops))
if not stops:
    raise SystemExit('no Stop payloads recorded')

payload = json.load(open(stops[0]))
tpath = payload['payload'].get('transcript_path')
block_wall = payload['wall']
print('transcript: %s' % tpath)
for s in stops:
    d = json.load(open(s))
    print('   %s  stop_hook_active=%s' % (
        os.path.basename(s), d['payload'].get('stop_hook_active')))

events = [json.loads(l) for l in open(os.path.expanduser(tpath)) if l.strip()]
print('\n%d transcript events' % len(events))

def ts(e):
    t = e.get('timestamp')
    if not t:
        return None
    return datetime.datetime.fromisoformat(t.replace('Z', '+00:00')).timestamp()

boundary = block_wall
before, after = [], []
for e in events:
    t = ts(e)
    (before if (t or 0) <= boundary else after).append(e)

def tools(evts):
    out = []
    for e in evts:
        msg = e.get('message') or {}
        content = msg.get('content')
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get('type') == 'tool_use':
                    inp = c.get('input') or {}
                    arg = inp.get('file_path') or inp.get('pattern') or inp.get('command') or ''
                    out.append('%s(%s)' % (c.get('name'), str(arg)[:70]))
    return out

def texts(evts, role='assistant'):
    out = []
    for e in evts:
        msg = e.get('message') or {}
        if msg.get('role') != role:
            continue
        content = msg.get('content')
        if isinstance(content, str):
            out.append(content)
        elif isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get('type') == 'text':
                    out.append(c['text'])
    return out

print('\n--- ROUND 1 (before the refusal) ---')
print('tool calls: %s' % (tools(before) or 'NONE'))
r1 = texts(before)
print('final text of round 1:\n%s' % ('\n'.join(r1)[-1200:] if r1 else 'NONE'))

print('\n--- ROUND 2 (after the refusal) ---')
t2 = tools(after)
print('tool calls: %s' % (t2 or 'NONE'))
r2 = texts(after)
body = '\n'.join(r2)
print('final text of round 2:\n%s' % (body[-3000:] if r2 else 'NONE'))

print('\n--- verdict inputs ---')
print('new tool calls after refusal : %d' % len(t2))
print('CLAIM: blocks                : %d' % len(re.findall(r'^CLAIM:', body, re.M)))
print('EVIDENCE: blocks             : %d' % len(re.findall(r'^EVIDENCE:', body, re.M)))
print('QUOTE: blocks                : %d' % len(re.findall(r'^QUOTE:', body, re.M)))
print('UNKNOWN: blocks              : %d' % len(re.findall(r'^UNKNOWN:', body, re.M)))
