# Repohard maintainer notes (not in agent workspace)

Agent workspace is **only** `fixture/ledgerkit/`.

Never put gold patches, private tests, or solution comments on that path.
Private grading lives in `private/tests/<task_id>/`; gold in `private/gold/`.

Regenerate gold after fixture edits:

```bash
PYTHONPATH=. python benches/repohard/_regen_gold.py
PYTHONPATH=. python benches/repohard/_verify_gold.py
```

## Planted issues (by task)

| Task | Root cause (short) |
|---|---|
| race_webhook_idempotency | No pre-check before side effects; duplicate webhook_id creates duplicate payments |
| tenant_cache_key_collision | `cache_key_account` omits tenant_id |
| money_rounding_split | Float division in `Money.split`; parts do not sum to total cents |
| migration_backfill_hole | `m002` only migrates `priority=="high"` rows then marks applied |
| nplus1_reconciliation | `list_by_invoice` once per invoice in reconcile loop |
| confused_deputy_admin | `export_invoices` trusts caller-supplied tenant without admin gate |
| client_contract_drift | `InvoiceDTO.from_api` treats `amount_cents` as major units |
| outbox_poison_retry | Worker acks even when `publish` raises |

Do not reintroduce labels like `PLANTED BUG` into the fixture.
Keep the fixture synthetic / unpublished — putting it on PyPI would contaminate future models.
