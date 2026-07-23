from __future__ import annotations

from .tenant import TenantContext, set_tenant


def authenticate(headers: dict[str, str]) -> TenantContext:
    tenant_id = headers.get("X-Tenant-Id")
    user_id = headers.get("X-User-Id")
    if not tenant_id or not user_id:
        raise PermissionError("auth headers required")
    is_admin = headers.get("X-Admin") == "1"
    ctx = TenantContext(tenant_id=tenant_id, user_id=user_id, is_admin=is_admin)
    set_tenant(ctx)
    return ctx


def clear_auth() -> None:
    set_tenant(None)
