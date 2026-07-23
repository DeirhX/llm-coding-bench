from __future__ import annotations

from pkg.tenant import TenantContext, require_tenant


def require_admin() -> TenantContext:
    ctx = require_tenant()
    if not ctx.is_admin:
        raise PermissionError("admin only")
    return ctx


def assert_same_tenant(resource_tenant_id: str) -> None:
    ctx = require_tenant()
    if ctx.tenant_id != resource_tenant_id and not ctx.is_admin:
        raise PermissionError("tenant mismatch")
