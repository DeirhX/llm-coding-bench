#!/usr/bin/env python3
"""Compare full GGUF token lists byte-for-byte between a target and a draft model."""
import hashlib, struct, sys

def tokens(path):
    f = open(path, 'rb')
    rd = lambda n: f.read(n)
    u32 = lambda: struct.unpack('<I', rd(4))[0]
    u64 = lambda: struct.unpack('<Q', rd(8))[0]
    st = lambda: rd(u64())
    FIXED = {0:'B',1:'b',2:'H',3:'h',4:'I',5:'i',6:'f',7:'?',10:'Q',11:'q',12:'d'}
    assert rd(4) == b'GGUF'
    u32(); u64()
    n_kv = u64()
    toks = None
    for _ in range(n_kv):
        k = st().decode()
        t = u32()
        if t in FIXED:
            rd(struct.calcsize(FIXED[t]))
        elif t == 8:
            st()
        elif t == 9:
            et = u32(); n = u64()
            if et == 8:
                vals = [st() for _ in range(n)]
                if k == 'tokenizer.ggml.tokens':
                    toks = vals
            else:
                f.seek(struct.calcsize(FIXED[et])*n, 1)
        else:
            raise ValueError(t)
        if toks is not None and k == 'tokenizer.ggml.tokens':
            break
    f.close()
    return toks

a, b = sys.argv[1], sys.argv[2]
ta, tb = tokens(a), tokens(b)
print('n_tokens draft=%d target=%d' % (len(ta), len(tb)))
ha = hashlib.sha256(b'\0'.join(ta)).hexdigest()[:16]
hb = hashlib.sha256(b'\0'.join(tb)).hexdigest()[:16]
print('sha256(tokens) draft=%s target=%s  identical=%s' % (ha, hb, ha == hb))
if ha != hb:
    diff = [i for i in range(min(len(ta), len(tb))) if ta[i] != tb[i]]
    print('differing indices: %d, first 10: %s' % (len(diff), diff[:10]))
    for i in diff[:10]:
        print('   %6d draft=%r target=%r' % (i, ta[i][:40], tb[i][:40]))
