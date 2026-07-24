# qwen3.6 repohard ablation (soft tasks /50)

task | baseline | ctx128k | finalize_r20 | predict24k | rounds80 | think_medium
--- | --- | --- | --- | --- | --- | ---
race_webhook_idempotency | 7 | 0 | 10 | 0 | 0 | 10
migration_backfill_hole | 0 | 0 | 0 | 0 | 0 | 10
nplus1_reconciliation | 0 | 0 | 0 | 0 | 0 | 10
confused_deputy_admin | 0 | 8 | 8 | 0 | 0 | 10
client_contract_drift | 0 | 10 | 10 | 10 | 10 | 0
TOTAL | 7 | 18 | 28 | 10 | 10 | 40

## Delta vs baseline

- **ctx128k**: 18/50 (+11 vs baseline 7)
- **finalize_r20**: 28/50 (+21 vs baseline 7)
- **predict24k**: 10/50 (+3 vs baseline 7)
- **rounds80**: 10/50 (+3 vs baseline 7)
- **think_medium**: 40/50 (+33 vs baseline 7)
