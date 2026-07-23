from __future__ import annotations

from typing import Any, Callable

from api.middleware import auth as auth_mw
from api import invoices, accounts, webhooks, admin, internal


ROUTES: dict[tuple[str, str], Callable[..., Any]] = {
    ("GET", "/health"): lambda **_: {"ok": True},
    ("GET", "/v1/invoices/{id}"): invoices.get_one,
    ("POST", "/v1/invoices"): invoices.create,
    ("GET", "/v1/accounts"): accounts.list_accounts,
    ("POST", "/v1/accounts"): accounts.create,
    ("POST", "/v1/webhooks/payment"): webhooks.payment,
    ("GET", "/v1/admin/export"): admin.export,
    ("GET", "/internal/export"): internal.export,
}


def handle(method: str, path: str, headers: dict[str, str] | None = None, body: dict | None = None) -> dict:
    headers = headers or {}
    body = body or {}
    auth_mw.apply(headers)
    # very small router
    for (m, pattern), fn in ROUTES.items():
        if m != method:
            continue
        if pattern.endswith("{id}"):
            prefix = pattern.rsplit("{id}", 1)[0]
            if path.startswith(prefix) and path != prefix.rstrip("/"):
                rid = path[len(prefix):]
                return fn(id=rid, body=body, headers=headers)
        elif path == pattern:
            return fn(body=body, headers=headers)
    return {"error": "not_found", "status": 404}
