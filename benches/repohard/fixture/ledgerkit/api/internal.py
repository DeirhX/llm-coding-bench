from __future__ import annotations

from service import admin_service
from pkg.tenant import require_tenant


def export(body=None, headers=None, **_):
    """Internal path used by sibling services."""
    require_tenant()
    body = body or {}
    rows = admin_service.export_invoices(body.get("tenant_id"))
    return {"status": 200, "invoices": rows}
