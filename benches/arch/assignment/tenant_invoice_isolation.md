---
id: tenant_invoice_isolation
title: Tenant isolation for invoices
family: invariant
max_score: 10
required_files: [service/invoice_service.py,store/invoice_repo.py]
---

Task: README claims I2 — invoice reads are tenant-scoped.
Find where tenant scoping IS enforced for invoices, and every bypass that can return or export another tenant's invoice data.

Return JSON:
{
  "enforced_at": ["module.func or path symbols"],
  "bypasses": ["module.func or path symbols"],
  "citations": ["path.py:symbol", ...]
}
