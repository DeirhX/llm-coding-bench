---
id: redesign_webhook_idempotency
title: Constrained fix: webhook idempotency
family: redesign
max_score: 10
required_files: [service/payment_service.py]
---

Task: Propose a minimal fix so payment webhooks cannot double-insert OrderPaid.
Constraints: touch at most 3 files; no new message broker; keep HTTP webhook entrypoint.

Return JSON:
{
  "touch_files": ["relative/paths.py"],
  "plan_steps": ["..."],
  "idempotency_change": "what moves before/after what",
  "citations": ["path.py:symbol", ...]
}
