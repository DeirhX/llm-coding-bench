# Archbench results (tools-first)

Fixture: shopapi · 9 tasks / 90 pts · tool budget 30/task

| Model | Score | Pass | Tool calls | Wall s |
|---|---:|---:|---:|---:|
| qwen3.5:35b-a3b-coding-bf16 | 85/90 | 9/9 | 48 | 99.2 |
| qwen3.6:35b-a3b-coding-bf16 | 84/90 | 9/9 | 92 | 154.3 |
| qwen3-coder-next:q8_0 | 84/90 | 9/9 | 184 | 209.3 |
| qwen3-coder:30b-a3b-fp16 | 83/90 | 9/9 | 87 | 97.4 |
| llama3.3:70b-instruct-q8_0 | 78/90 | 8/9 | 44 | 455.6 |
| devstral:24b-small-2505-fp16 | 78/90 | 8/9 | 69 | 579.0 |
| qwen2.5-coder:32b-instruct-q8_0 | 75/90 | 7/9 | 50 | 211.1 |
| gpt-oss:120b | 62/90 | 4/9 | 51 | 653.0 |
| north-mini-code-1.0:bf16 | 4/90 | 0/9 | 2 | 285.3 |
| deepseek-r1:70b-llama-distill-q8_0 | 1/30 | 0/3 | 0 | 253.5 |

Tie cluster (claim probe): qwen3.5:35b-a3b-coding-bf16, qwen3.6:35b-a3b-coding-bf16, qwen3-coder-next:q8_0, qwen3-coder:30b-a3b-fp16

## Claim probe (15 T/F + evidence)

| Model | Score | Correct | Wrong | Missing | Wall s |
|---|---:|---:|---:|---:|---:|
| devstral:24b-small-2505-fp16 | 18/18 | 15 | 0 | 0 | 178.97 |
| qwen3-coder-next:q8_0 | 18/18 | 15 | 0 | 0 | 48.29 |
| qwen3.5:35b-a3b-coding-bf16 | 18/18 | 15 | 0 | 0 | 30.82 |
| qwen3.6:35b-a3b-coding-bf16 | 18/18 | 15 | 0 | 0 | 39.24 |
| qwen2.5-coder:32b-instruct-q8_0 | 16/18 | 13 | 2 | 0 | 335.16 |
| gpt-oss:120b | 15/18 | 13 | 2 | 0 | 232.75 |
| north-mini-code-1.0:bf16 | 7/18 | 7 | 8 | 0 | 17.1 |
| qwen3-coder:30b-a3b-fp16 | 3/18 | 0 | 0 | 15 | 31.84 |
| llama3.3:70b-instruct-q8_0 | — | — | — | — | abandoned (hung) |

## Per-task scores

| Task | qwen3.5 | qwen3-coder-next | qwen3.6 | qwen3-coder | devstral | llama3.3 | qwen2.5-coder | gpt-oss | north-mini-code-1. | deepseek-r1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| chain_delete_order | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 8/10 | 10/10 | 4/10 | 0/10 | 1/10 |
| chain_payment_webhook | 10/10 | 9/10 | 9/10 | 8/10 | 8/10 | 6/10 | 5/10 | 6/10 | 0/10 | 0/10 |
| tenant_invoice_isolation | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 9/10 | 10/10 | 10/10 | 0/10 | 0/10 |
| invariant_doc_vs_code | 10/10 | 10/10 | 10/10 | 10/10 | 8/10 | 10/10 | 8/10 | 6/10 | 2/10 | — |
| review_list_orders_n1 | 9/10 | 9/10 | 9/10 | 9/10 | 9/10 | 9/10 | 9/10 | 5/10 | 2/10 | — |
| review_cache_on_paid | 9/10 | 9/10 | 9/10 | 9/10 | 9/10 | 9/10 | 9/10 | 9/10 | 0/10 | — |
| incident_duplicate_order_paid | 9/10 | 9/10 | 9/10 | 9/10 | 6/10 | 9/10 | 6/10 | 4/10 | 0/10 | — |
| incident_outbox_ack_order | 9/10 | 9/10 | 9/10 | 9/10 | 9/10 | 9/10 | 9/10 | 9/10 | 0/10 | — |
| redesign_webhook_idempotency | 9/10 | 9/10 | 9/10 | 9/10 | 9/10 | 9/10 | 9/10 | 9/10 | 0/10 | — |

## Judgment

Archbench leader: **qwen3.5:35b-a3b-coding-bf16** at 85/90 (9/9 pass) in 99.2s with 48 tool calls. Among near-top scorers, fastest is **qwen3-coder:30b-a3b-fp16** (97.4s, 87 tools). Claim probe leader: **qwen3.5:35b-a3b-coding-bf16** 15/15 correct (score 18/18). Claim probe mean correct: 11.6/15 — this is the discriminative ruler when arch scores cluster. Score spread top−bottom: 81 pts on /90. Useful separation.
