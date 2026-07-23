# Maintainer notes (not part of the model workspace)

Shared fixture for **arch** and **claim** benches.

These bugs are intentional. Do **not** reintroduce `PLANTED BUG` comments into
`fixture/shopapi/` — models grepping for that string is a free win.

Cursor/Ollama workspace root for agent runs: `benches/shopapi/fixture/shopapi`.

| Location | Issue |
|---|---|
| `service/order_service.py` `list_orders` | N+1 via per-row `get_order` |
| `service/order_service.py` `mark_paid` | status→paid without cache invalidate (I4) |
| `service/order_service.py` `get_order` | cache hit skips tenant re-check |
| `service/invoice_service.py` `get_invoice` / `admin_export_invoices` | tenant bypass (I2) |
| `store/invoice_repo.py` `get_by_id` / `export_all` | no tenant filter |
| `service/payment_service.py` `handle_payment_webhook` | outbox before `_mark_processed` (I3) |
| `worker/outbox_worker.py` `process_once` | ack before publish |
| `service/legacy_order.py` | decoy — not on DELETE path (claim c11) |
