---
id: review_list_orders_n1
title: Review: list_orders performance smell
family: review
max_score: 10
required_files: [service/order_service.py]
---

Task: Architecture review of service/order_service.py list_orders (and callees as needed).
Focus on correctness of auth/tenant handling and performance smells.

Return JSON:
{
  "findings": [{"id": "n_plus_1", "severity": "medium|high|low", "summary": "..."}],
  "max_severity": "medium",
  "auth_ok": true,
  "citations": ["path.py:symbol", ...]
}
Only report real issues. Do not invent auth failures if auth is fine.
