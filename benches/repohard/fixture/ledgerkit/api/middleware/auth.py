from __future__ import annotations

from pkg.tenant import TenantContext, set_tenant, clear_tenant


def apply(headers: dict[str, str]) -> None:
    clear_tenant()
    tid = headers.get("X-Tenant-Id") or headers.get("x-tenant-id")
    if not tid:
        return
    is_admin = (headers.get("X-Admin") or headers.get("x-admin") or "").lower() in ("1", "true", "yes")
    uid = headers.get("X-User-Id") or headers.get("x-user-id") or "anon"
    set_tenant(TenantContext(tenant_id=tid, is_admin=is_admin, user_id=uid))
