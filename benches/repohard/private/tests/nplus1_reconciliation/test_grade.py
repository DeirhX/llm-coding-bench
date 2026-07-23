from __future__ import annotations

from store import db, cache, invoice_repo, payment_repo, account_repo
from pkg.tenant import set_tenant, TenantContext, clear_tenant
from service.reconcile_service import reconcile_tenant


def setup_function(_=None):
    db.reset()
    cache.reset()
    clear_tenant()
    payment_repo.reset_stats()
    account_repo.upsert({"id": "a1", "tenant_id": "t1", "name": "A"})
    for i in range(40):
        invoice_repo.upsert(
            {
                "id": f"inv{i}",
                "tenant_id": "t1",
                "account_id": "a1",
                "amount_cents": 100,
                "currency": "USD",
                "status": "paid" if i % 2 == 0 else "open",
                "lines": [],
            }
        )
        if i % 2 == 0:
            payment_repo.upsert(
                {
                    "id": f"pay{i}",
                    "tenant_id": "t1",
                    "invoice_id": f"inv{i}",
                    "amount_cents": 100,
                }
            )


def test_reconcile_avoids_per_invoice_payment_lookups():
    set_tenant(TenantContext(tenant_id="t1"))
    result = reconcile_tenant()
    assert result["invoice_count"] == 40
    assert result["matched"] == 20
    # allow a tiny constant number of list_by_invoice calls, not O(n)
    assert payment_repo.stats()["list_by_invoice"] <= 2, payment_repo.stats()


def test_reconcile_gap_detection():
    payment_repo.upsert(
        {"id": "payX", "tenant_id": "t1", "invoice_id": "inv1", "amount_cents": 50}
    )
    set_tenant(TenantContext(tenant_id="t1"))
    result = reconcile_tenant()
    assert "inv1" in result["gaps"]
