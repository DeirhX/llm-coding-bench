# Ledger backfill leaves legacy rows

Migration `002_backfill_ledger` is marked applied, but many rows remain in
`ledger_legacy` with `migrated=False`. Reconciliation still misses those
amounts.

## Expected behavior

Running the migration must move **all** unmigrated legacy ledger rows into
`ledger` and mark them migrated. `schema_meta` may still record the migration.

## Constraints

- Fix `store/migrations/m002_backfill_ledger.py` (and helpers if needed).
- Do not drop legacy table.
