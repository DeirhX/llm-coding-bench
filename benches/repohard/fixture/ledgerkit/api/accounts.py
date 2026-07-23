from __future__ import annotations

from service import account_service
from pkg.tenant import require_tenant


def list_accounts(body=None, headers=None, **_):
    require_tenant()
    return {"status": 200, "accounts": account_service.list_accounts()}


def create(body=None, headers=None, **_):
    body = body or {}
    row = account_service.create_account(body["id"], body.get("name", ""))
    return {"status": 201, "account": row}
