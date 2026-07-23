---
id: incident_duplicate_order_paid
title: Incident: duplicate OrderPaid
family: incident
max_score: 10
required_files: [service/payment_service.py]
---

Task: Symptom: after provider retries, customers sometimes get duplicate OrderPaid outbox events for one webhook_id.
Find the root cause in this repo and the primary function to fix.

Return JSON:
{
  "root_cause": "brief",
  "fix_function": "module.func",
  "fix_functions": ["module.func"],
  "citations": ["path.py:symbol", ...]
}
