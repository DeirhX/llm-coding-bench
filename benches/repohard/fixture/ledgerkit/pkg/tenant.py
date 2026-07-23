from __future__ import annotations

from dataclasses import dataclass
from contextvars import ContextVar

_current: ContextVar["TenantContext | None"] = ContextVar("tenant", default=None)


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    is_admin: bool = False
    user_id: str = "system"


def set_tenant(ctx: TenantContext) -> None:
    _current.set(ctx)


def clear_tenant() -> None:
    _current.set(None)


def require_tenant() -> TenantContext:
    ctx = _current.get()
    if ctx is None:
        raise PermissionError("no tenant context")
    return ctx


def current_tenant() -> TenantContext | None:
    return _current.get()
