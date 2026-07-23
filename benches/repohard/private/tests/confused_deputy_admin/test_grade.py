from __future__ import annotations

from store import db, cache, invoice_repo, account_repo
from pkg.tenant import set_tenant, TenantContext, clear_tenant
from service import admin_service
from api.app import handle


def setup_function(_=None):
    db.reset()
    cache.reset()
    clear_tenant()
    for tid in ("t_victim", "t_attacker"):
        account_repo.upsert({"id": f"a_{tid}", "tenant_id": tid, "name": tid})
        invoice_repo.upsert(
            {
                "id": f"inv_{tid}",
                "tenant_id": tid,
                "account_id": f"a_{tid}",
                "amount_cents": 999,
                "currency": "USD",
                "status": "open",
                "lines": [],
            }
        )


def test_non_admin_cannot_export_other_tenant():
    set_tenant(TenantContext(tenant_id="t_attacker", is_admin=False))
    try:
        admin_service.export_invoices("t_victim")
        assert False, "expected PermissionError"
    except PermissionError:
        pass


def test_non_admin_can_export_own():
    set_tenant(TenantContext(tenant_id="t_attacker", is_admin=False))
    rows = admin_service.export_invoices("t_attacker")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "t_attacker"


def test_admin_can_export_any():
    set_tenant(TenantContext(tenant_id="t_attacker", is_admin=True))
    rows = admin_service.export_invoices("t_victim")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "t_victim"


def test_internal_route_blocks_cross_tenant():
    resp = handle(
        "GET",
        "/internal/export",
        headers={"X-Tenant-Id": "t_attacker"},
        body={"tenant_id": "t_victim"},
    )
    assert resp.get("status") in (403, None) or "error" in resp or resp.get("status") == 403
    # also accept raised path turned into error by handle — if handle doesn't catch, call service
    if resp.get("status") == 200:
        assert all(r["tenant_id"] != "t_victim" for r in resp.get("invoices", []))
