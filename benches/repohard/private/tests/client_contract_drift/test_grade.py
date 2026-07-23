from __future__ import annotations

from store import db, cache
from pkg.tenant import set_tenant, TenantContext, clear_tenant
from service import account_service, invoice_service
from pkg.money import Money
from client.models import InvoiceDTO
from client.http import LedgerClient
from api.app import handle


def setup_function(_=None):
    db.reset()
    cache.reset()
    clear_tenant()


def test_from_api_uses_cents():
    dto = InvoiceDTO.from_api(
        {
            "id": "i1",
            "tenant_id": "t1",
            "account_id": "a1",
            "amount_cents": 2599,
            "currency": "USD",
            "status": "open",
        }
    )
    assert abs(dto.amount - 25.99) < 1e-9


def test_client_roundtrip():
    set_tenant(TenantContext(tenant_id="t1"))
    account_service.create_account("a1", "A")
    inv = invoice_service.create_invoice("a1", Money(1234))
    clear_tenant()
    client = LedgerClient(handle)
    dto = client.get_invoice(inv["id"], headers={"X-Tenant-Id": "t1"})
    assert abs(dto.amount - 12.34) < 1e-9
