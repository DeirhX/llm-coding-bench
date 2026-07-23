# ShopAPI (fixture)

Tiny order/payment service used by archbench.

Claimed invariants (docs — not always true in code):

- I1: Every mutating order API writes an outbox event before returning.
- I2: Invoice reads are tenant-scoped.
- I3: Payment webhooks are idempotent (no double charge / duplicate OrderPaid).
- I4: Order cache entries are invalidated on any status change.
