---
id: review_cache_on_paid
title: Review: cache vs status change (I4)
family: review
max_score: 10
required_files: [service/order_service.py]
---

Task: Does mark_paid uphold README invariant I4 (cache invalidated on status change)?

Return JSON:
{
  "i4_holds": false,
  "findings": [{"summary": "...", "severity": "high|medium|low"}],
  "citations": ["path.py:symbol", ...]
}
