from __future__ import annotations

from store import db, cache, account_repo
from pkg.tenant import set_tenant, TenantContext, clear_tenant
from service import account_service


def setup_function(_=None):
    db.reset()
    cache.reset()
    clear_tenant()
    account_repo.upsert({"id": "shared", "tenant_id": "t_a", "name": "Alpha"})
    account_repo.upsert({"id": "shared", "tenant_id": "t_b", "name": "Beta"})


def test_cache_does_not_leak_across_tenants():
    set_tenant(TenantContext(tenant_id="t_a"))
    a = account_service.get_account("shared")
    assert a is not None and a["name"] == "Alpha"
    clear_tenant()

    set_tenant(TenantContext(tenant_id="t_b"))
    b = account_service.get_account("shared")
    assert b is not None
    assert b["tenant_id"] == "t_b"
    assert b["name"] == "Beta"
    clear_tenant()

    set_tenant(TenantContext(tenant_id="t_a"))
    a2 = account_service.get_account("shared")
    assert a2["tenant_id"] == "t_a"
    assert a2["name"] == "Alpha"


def test_cache_key_includes_tenant():
    from store import cache as c

    # key helper must accept tenant
    key_a = c.cache_key_account("t_a", "shared") if _arity() == 2 else None
    assert key_a is not None
    key_b = c.cache_key_account("t_b", "shared")
    assert key_a != key_b


def _arity():
    import inspect
    from store import cache as c

    return len(inspect.signature(c.cache_key_account).parameters)
