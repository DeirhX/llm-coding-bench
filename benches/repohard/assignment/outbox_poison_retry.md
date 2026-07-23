# Outbox acks poison events

`worker.outbox_worker.process_once` acknowledges outbox rows even when
`publish` raises. Poison payloads disappear and are never retried.

## Expected behavior

Only ack (and mark published) after a successful publish. Failed publishes
must leave the row unacked for a later retry.

## Constraints

- Keep batch claiming behavior.
- Do not swallow all errors silently without leaving the row retryable.
