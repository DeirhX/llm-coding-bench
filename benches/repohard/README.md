# Repohard

Hybrid large-repo coding bench over synthetic **ledgerkit**.

1. Agent explores `fixture/ledgerkit/` with tools (workspace jail).
2. Agent returns a unified diff in `<arch_final>{"patch":"..."}</arch_final>`.
3. Runner applies the patch to a fresh fixture copy and runs `private/tests/<task>/`.

`private/` is never on the agent path. See `MAINTAINER_NOTES.md`.

```bash
BENCH_SELFTEST=1 python -m benches.repohard
BENCH_MODEL='…' python run.py run repohard
```
