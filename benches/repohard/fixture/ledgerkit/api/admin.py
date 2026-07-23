from __future__ import annotations

from pkg.authz import require_admin
from service import admin_service


def export(body=None, headers=None, **_):
    require_admin()
    body = body or {}
    rows = admin_service.export_invoices(body.get("tenant_id"))
    return {"status": 200, "invoices": rows}
