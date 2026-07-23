# Repohard maintainer notes (not in agent workspace)

Canonical fixture: `fixture/ledgerkit/`. The harness grades and Cursor ask-mode
runs on a **per-task temp copy** (`fresh_fixture_copy`) so agents cannot poison
the canonical tree mid-suite.

Never put gold patches, private tests, or solution comments under the fixture.
Private grading lives in `private/tests/<task_id>/`; gold in `private/gold/`.

Results must persist the full unified diff in `answer.patch` (not only
`raw_content[:8000]` / `patch_preview`) so rescoring stays faithful.

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

## Grading notes (assignment-aligned)

Private tests score `round(10 * passed / total)` after patch apply. Full credit
matches **assignment semantics**, not gold cosmetics:

- **race_webhook_idempotency** — require idempotent side effects (one payment /
  entitlement). A `"duplicate"` status string is nice-to-have, not required.
- **confused_deputy_admin** — require non-admin cannot export another tenant.
  Mapping `PermissionError` → HTTP 403 in `api/internal.py` is nice-to-have;
  a raised `PermissionError` from the service layer counts as blocked.

Rescore saved runs after grader changes:

```bash
python3.14 -m benches.repohard.rescore
```

Do not reintroduce labels like `PLANTED BUG` into the fixture.
Keep the fixture synthetic / unpublished — putting it on PyPI would contaminate future models.
