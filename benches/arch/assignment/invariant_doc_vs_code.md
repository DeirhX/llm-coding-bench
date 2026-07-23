---
id: invariant_doc_vs_code
title: Which README invariants fail?
family: invariant
max_score: 10
required_files: [README.md,service/payment_service.py,service/invoice_service.py]
---

Task: README lists invariants I1–I4. Which are violated by the current code?
Do NOT mark an invariant violated unless you found concrete evidence.

Return JSON:
{
  "violated_invariants": ["I2", "..."],
  "evidence": {"I2": "brief reason", "...": "..."},
  "citations": ["path.py:symbol", ...]
}
