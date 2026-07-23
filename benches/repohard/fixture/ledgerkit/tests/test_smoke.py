from __future__ import annotations

from store import db, cache
from pkg.tenant import set_tenant, TenantContext, clear_tenant
from pkg.money import Money
from service import account_service, invoice_service, ledger_service
from api.app import handle


def setup_function(_=None):
    db.reset()
    cache.reset()
    clear_tenant()


def test_create_account_and_invoice():
    set_tenant(TenantContext(tenant_id="t1"))
    account_service.create_account("a1", "Alpha")
    inv = invoice_service.create_invoice("a1", Money(2500))
    assert inv["amount_cents"] == 2500
    assert inv["tenant_id"] == "t1"


def test_api_health():
    assert handle("GET", "/health")["ok"] is True


def test_money_add():
    assert (Money(10) + Money(5)).cents == 15


def test_ledger_post():
    set_tenant(TenantContext(tenant_id="t1"))
    e = ledger_service.post_entry(Money(100), "memo")
    assert e["cents"] == 100
