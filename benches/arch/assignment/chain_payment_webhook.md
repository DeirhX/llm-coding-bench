---
id: chain_payment_webhook
title: Call chain: POST /webhooks/payment
family: call_chain
max_score: 10
required_files: [api/webhooks.py,service/payment_service.py]
---

Task: Trace POST /webhooks/payment for a new (non-duplicate) webhook.

Return JSON:
{
  "chain": ["module.func", ...],
  "side_effects": ["short strings"],
  "citations": ["path.py:symbol", ...]
}
