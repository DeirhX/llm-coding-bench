# MiniHarness

Local harness stub used to drive coding-model evaluations.

## Layout

- `runner.py` — per-task loop and summary write
- `chat/` — HTTP chat client stack (`facade` → `wrap` → `api`)
- `solver/` — reference solvers (SAT, mini-SQL)
- `think/` — thinking-loop detector
- `util/` — subprocess timeout helper, path helpers
- `warmup.py` — cold-start ping before a suite
- `schema.py` — toy tables for the SQL selftest
- `compat/` — legacy re-exports

See `MAINTAINER_NOTES.md` for known issues.
