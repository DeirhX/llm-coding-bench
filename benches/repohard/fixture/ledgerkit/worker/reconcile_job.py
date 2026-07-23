from __future__ import annotations

from service.reconcile_service import reconcile_tenant
from pkg.tenant import set_tenant, TenantContext, clear_tenant


def run_for(tenant_id: str) -> dict:
    set_tenant(TenantContext(tenant_id=tenant_id))
    try:
        return reconcile_tenant()
    finally:
        clear_tenant()
