from __future__ import annotations

from service import invoice_service
from pkg.money import Money
from pkg.tenant import require_tenant


def get_one(id: str, body=None, headers=None):
    require_tenant()
    inv = invoice_service.get_invoice(id)
    if inv is None:
        return {"error": "not_found", "status": 404}
    return {"status": 200, "invoice": invoice_service.serialize_public(inv)}


def create(body=None, headers=None, **_):
    require_tenant()
    body = body or {}
    amount = Money(int(body["amount_cents"]), body.get("currency", "USD"))
    inv = invoice_service.create_invoice(body["account_id"], amount, body.get("lines"))
    return {"status": 201, "invoice": invoice_service.serialize_public(inv)}
