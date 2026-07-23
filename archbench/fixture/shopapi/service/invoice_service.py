from __future__ import annotations

from typing import Any

from pkg.tenant import require_tenant
from store import invoice_repo


def list_invoices() -> list[dict[str, Any]]:
    tenant = require_tenant()
    return invoice_repo.list_by_tenant(tenant.tenant_id)


def get_invoice(invoice_id: str) -> dict[str, Any]:
    """PLANTED BYPASS (I2): returns invoice without checking tenant_id."""
    require_tenant()  # ensures caller is authenticated, but does not scope
    row = invoice_repo.get_by_id(invoice_id)
    if not row:
        raise KeyError(invoice_id)
    return row


def admin_export_invoices() -> list[dict[str, Any]]:
    """PLANTED BYPASS (I2): admin path exports all tenants' invoices."""
    tenant = require_tenant()
    if not tenant.is_admin:
        raise PermissionError("admin only")
    return invoice_repo.export_all()
