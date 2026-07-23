from __future__ import annotations

from typing import Any

from store import account_repo, invoice_repo, audit_repo
from pkg.tenant import require_tenant


def export_invoices(target_tenant_id: str | None = None) -> list[dict[str, Any]]:
    """Internal export used by ops dashboards."""
    ctx = require_tenant()
    tid = target_tenant_id or ctx.tenant_id
    rows = invoice_repo.list_by_tenant(tid)
    audit_repo.write(ctx.tenant_id, "export_invoices", {"target": tid, "n": len(rows)})
    return rows


def delete_account(account_id: str) -> bool:
    ctx = require_tenant()
    row = account_repo.get(account_id)
    if row is None:
        return False
    if row.get("tenant_id") != ctx.tenant_id and not ctx.is_admin:
        raise PermissionError("cannot delete foreign account")
    from store import db

    db.delete("accounts", account_id)
    return True
