from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    user_id: str
    is_admin: bool = False


_CURRENT: TenantContext | None = None


def set_tenant(ctx: TenantContext | None) -> None:
    global _CURRENT
    _CURRENT = ctx


def current_tenant() -> TenantContext | None:
    return _CURRENT


def require_tenant() -> TenantContext:
    ctx = current_tenant()
    if ctx is None:
        raise PermissionError("missing tenant context")
    return ctx
