# Audittrap

Hybrid bench: claim battery + repair tickets over synthetic `miniharness`.
Tickets are written as on-call reports; some are real, some are not. Prompts
do not label which.

## Score

| Part | Tasks | Points |
|------|-------|--------|
| Claims | 1 × 18 (+≤3 evidence) | ≤21 |
| Repair | 6 × 10 | 60 |
| **Total** | **7** | **≤81** |

## Run

```bash
BENCH_SELFTEST=1 .venv/bin/python -m benches.audittrap
BENCH_PROVIDER=cursor BENCH_MODEL='claude-opus-5-thinking-high' .venv/bin/python -m benches.audittrap
```

Ollama / OpenAI-compatible (ds4) runs prepend a `system` message from
`system_local.md` by default (override with `BENCH_SYSTEM_PROMPT_FILE`).
Variants under `benches/audittrap/`:
- `system_local_v1.md` — early checklist (includes a few bench-flavored asides)
- `system_local_general.md` — general audit rules + mandatory diff-construction procedure
- `system_patch_hygiene.md` — standalone patch-emission procedure (live agents / composition)

Disable with `BENCH_SYSTEM_PROMPT=0`. Cursor runs keep their own agent system
prompt; harness only sends the user preamble.

Private gold/pytest under `private/` — never on the agent path.
