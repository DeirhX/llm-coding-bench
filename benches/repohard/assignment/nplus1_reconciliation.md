# Reconciliation N+1 payment lookups

`service.reconcile_service.reconcile_tenant` is too slow on tenants with
hundreds of invoices. Profiling shows one `payment_repo.list_by_invoice`
call per invoice.

## Expected behavior

Reconcile a tenant with many invoices using **O(1)** calls to load payments
for the whole tenant (or a single bulk helper), not one call per invoice.
Functional matching results must remain correct.

`store.payment_repo` exposes `list_by_invoice` and may be extended with a
bulk helper. A call counter on `list_by_invoice` is used in grading — keep
per-invoice calls near zero for large sets.
