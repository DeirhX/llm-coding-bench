# Payment webhook double-applies under concurrency

Ops reports that when the payment provider retries a `payment.succeeded`
webhook at the same time as the original delivery, **two payments** and
**two entitlement activations** appear for a single `webhook_id`.

## Expected behavior

`service.payment_service.handle_payment_webhook` must be idempotent on
`webhook_id`: concurrent callers with the same id produce exactly one
payment row and one entitlement activation.

## Constraints

- Fix the production code under `service/` / `store/` as needed.
- Do not weaken webhook processing (still mark paid + emit outbox on first success).
- Deliver a unified diff of your changes.
