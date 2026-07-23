from __future__ import annotations

from typing import Any

from store import invoice_repo, payment_repo
from pkg.tenant import require_tenant


def reconcile_tenant() -> dict[str, Any]:
    """Match open invoices to payments."""
    ctx = require_tenant()
    invoices = invoice_repo.list_by_tenant(ctx.tenant_id)
    matched = 0
    gaps = []
    for inv in invoices:
        pays = payment_repo.list_by_invoice(inv["id"])
        paid = sum(p.get("amount_cents", 0) for p in pays)
        if paid >= inv.get("amount_cents", 0) and inv.get("status") == "paid":
            matched += 1
        elif paid > 0 and inv.get("status") != "paid":
            gaps.append(inv["id"])
    return {"matched": matched, "gaps": gaps, "invoice_count": len(invoices)}


def reconcile_all_tenants(tenant_ids: list[str]) -> list[dict[str, Any]]:
    from pkg.tenant import set_tenant, TenantContext, clear_tenant

    out = []
    for tid in tenant_ids:
        set_tenant(TenantContext(tenant_id=tid))
        out.append({"tenant_id": tid, **reconcile_tenant()})
        clear_tenant()
    return out
