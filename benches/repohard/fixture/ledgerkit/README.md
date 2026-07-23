# LedgerKit

Multi-tenant billing / ledger service (synthetic fixture for benchmarks).

## Layout

- `api/` — HTTP-ish handlers (in-process, no real server)
- `service/` — domain services
- `store/` — persistence, cache, outbox
- `worker/` — async processors
- `pkg/` — shared primitives (tenant, money, events)
- `client/` — typed client used by sibling services
- `billing/` — tax / FX helpers
- `ops/` — health and metrics

## Invariants (docs)

I1. Webhook side effects are idempotent on `webhook_id`.
I2. Cache entries are tenant-scoped.
I3. Money is integer cents end-to-end.
I4. Migrations leave no unread legacy rows.
I5. Internal admin paths authorize the caller's tenant (or explicit admin role).
I6. Public JSON uses `amount_cents` (int).
I7. Outbox rows are only acked after successful publish.

Run smoke tests: `python -m pytest tests -q` from this directory.
