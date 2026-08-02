"""Separate stored KV from transient prefill buffers: peak ~ a + b*total + c*left + d*cached."""
import re

log = "/Users/deirh/.ollama/logs/server.log"
hit = re.compile(r'msg="cache hit" total=(\d+) matched=(\d+) cached=(\d+) left=(\d+)')
peak = re.compile(r'msg="peak memory" size="([\d.]+) GiB"')
rows, pending = [], None
for line in open(log, errors="replace"):
    m = hit.search(line)
    if m:
        pending = [int(g) for g in m.groups()]
        continue
    m = peak.search(line)
    if m and pending:
        rows.append(pending + [float(m.group(1))])
        pending = None
rows = rows[-600:]

def ols(cols, y):
    k = len(cols)
    X = [[1.0] + [c[i] for c in cols] for i in range(len(y))]
    n, p = len(y), k + 1
    A = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(p)] for a in range(p)]
    v = [sum(X[i][a] * y[i] for i in range(n)) for a in range(p)]
    for i in range(p):                      # gaussian elimination, tiny system
        piv = max(range(i, p), key=lambda r: abs(A[r][i]))
        A[i], A[piv] = A[piv], A[i]; v[i], v[piv] = v[piv], v[i]
        for r in range(p):
            if r == i: continue
            f = A[r][i] / A[i][i]
            for c in range(i, p): A[r][c] -= f * A[i][c]
            v[r] -= f * v[i]
    beta = [v[i] / A[i][i] for i in range(p)]
    fit = [sum(b * X[i][j] for j, b in enumerate(beta)) for i in range(n)]
    ss = sum((y[i] - fit[i]) ** 2 for i in range(n))
    tot = sum((y[i] - sum(y) / n) ** 2 for i in range(n))
    return beta, 1 - ss / tot, (ss / (n - p)) ** 0.5

total = [r[0] for r in rows]; matched = [r[1] for r in rows]
cached = [r[2] for r in rows]; left = [r[3] for r in rows]; y = [r[4] for r in rows]
print("%d requests, tokens %d..%d" % (len(rows), min(total), max(total)))
for name, cols in (("total", [total]), ("total+left", [total, left]),
                   ("total+left+cached", [total, left, cached])):
    beta, r2, sd = ols(cols, y)
    parts = " ".join("%s=%.4f MiB/tok" % (n, b * 1024)
                     for n, b in zip(name.split("+"), beta[1:]))
    print("%-18s base=%.2f GiB  %s  R2=%.3f  sd=%.2f GiB" % (name, beta[0], parts, r2, sd))

per_layer = 2 * 16 * 256                     # K and V, 16 kv heads, head_dim 256, per element
for label, layers, width in (("60 layers bf16", 60, 2), ("60 layers 8-bit", 60, 1),
                             ("10 full layers bf16", 10, 2), ("10 full + 50 windowed", 10, 2)):
    print("  %-22s %.3f MiB/token" % (label, layers * per_layer * width / 1048576))
