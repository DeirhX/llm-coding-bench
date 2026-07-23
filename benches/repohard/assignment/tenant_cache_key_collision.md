# Account cache leaks across tenants

Two tenants can create accounts with the same `account_id` (ids are only
unique within a tenant). After tenant A loads an account, tenant B sometimes
sees A's account data from cache.

## Expected behavior

Cached account reads must be tenant-scoped. A cache fill by tenant A must
never be returned to tenant B for the same account id.

## Constraints

- Inspect `store/cache.py` and `service/account_service.py`.
- Fix without removing caching entirely.
