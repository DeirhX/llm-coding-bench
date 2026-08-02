#!/usr/bin/env python3
"""Read GGUF key-value metadata without mapping tensor data.

Only the header is touched, so a 62 GB file costs the same as a 1 GB one.
"""
import struct, sys

T_UINT8,T_INT8,T_UINT16,T_INT16,T_UINT32,T_INT32,T_FLOAT32,T_BOOL,T_STRING,T_ARRAY,T_UINT64,T_INT64,T_FLOAT64 = range(13)
FIXED = {T_UINT8:'B',T_INT8:'b',T_UINT16:'H',T_INT16:'h',T_UINT32:'I',T_INT32:'i',
         T_FLOAT32:'f',T_BOOL:'?',T_UINT64:'Q',T_INT64:'q',T_FLOAT64:'d'}

class R:
    def __init__(self, f):
        self.f = f
    def raw(self, n):
        b = self.f.read(n)
        if len(b) != n:
            raise EOFError
        return b
    def u32(self):
        return struct.unpack('<I', self.raw(4))[0]
    def u64(self):
        return struct.unpack('<Q', self.raw(8))[0]
    def s(self):
        return self.raw(self.u64()).decode('utf-8', 'replace')
    def val(self, t):
        if t in FIXED:
            fmt = FIXED[t]
            return struct.unpack('<'+fmt, self.raw(struct.calcsize(fmt)))[0]
        if t == T_STRING:
            return self.s()
        if t == T_ARRAY:
            et = self.u32()
            n = self.u64()
            if et == T_STRING:
                head = [self.s() for _ in range(min(n, 8))]
                # skip the rest without materialising
                for _ in range(n - len(head)):
                    self.f.seek(self.u64(), 1)
                return ('array', 'string', n, head)
            fmt = FIXED[et]
            sz = struct.calcsize(fmt)
            head = list(struct.unpack('<'+fmt*min(n, 8), self.raw(sz*min(n, 8))))
            self.f.seek(sz*(n-len(head)), 1)
            return ('array', et, n, head)
        raise ValueError('type %d' % t)

def read(path, want):
    out = {}
    with open(path, 'rb') as f:
        r = R(f)
        magic = r.raw(4)
        if magic != b'GGUF':
            return {'_error': 'not gguf: %r' % magic}
        out['_version'] = r.u32()
        out['_n_tensors'] = r.u64()
        n_kv = r.u64()
        for _ in range(n_kv):
            k = r.s()
            t = r.u32()
            v = r.val(t)
            if any(w in k for w in want):
                out[k] = v
    return out

if __name__ == '__main__':
    want = ['tokenizer', 'general.architecture', 'general.name', 'context_length',
            'embedding_length', 'block_count', 'head_count', 'key_length',
            'value_length', 'vocab_size', 'general.file_type']
    for p in sys.argv[1:]:
        print('==', p)
        for k, v in read(p, want).items():
            if isinstance(v, tuple) and v[0] == 'array':
                print('   %-46s array[%s] n=%d head=%s' % (k, v[1], v[2], v[3][:4]))
            else:
                print('   %-46s %s' % (k, v))
