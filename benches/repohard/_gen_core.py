#!/usr/bin/env python3.14
"""Append ledgerkit core services / api / workers / client / smoke tests."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent / "fixture" / "ledgerkit"


def w(rel: str, body: str) -> None:
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    text = body.lstrip("\n") if body.startswith("\n") else body
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def main() -> None:
    # --- services ---
    w(
        "service/account_service.py",
        '''
from __future__ import annotations

from typing import Any

from store import account_repo, cache
from pkg.tenant import require_tenant


def get_account(account_id: str) -> dict[str, Any] | None:
    key = cache.cache_key_account(account_id)
    hit = cache.get(key)
    if hit is not None:
        return hit
    row = account_repo.get(account_id)
    if row is None:
        return None
    # cache full row without verifying caller tenant matches
    cache.set(key, row)
    return row


def create_account(account_id: str, name: str) -> dict[str, Any]:
    ctx = require_tenant()
    row = {"id": account_id, "tenant_id": ctx.tenant_id, "name": name}
    return account_repo.upsert(row)


def list_accounts() -> list[dict[str, Any]]:
    ctx = require_tenant()
    return account_repo.list_by_tenant(ctx.tenant_id)
''',
    )

    w(
        "service/invoice_service.py",
        '''
from __future__ import annotations

from typing import Any
import uuid

from store import invoice_repo, cache
from pkg.tenant import require_tenant
from pkg.money import Money


def create_invoice(account_id: str, amount: Money, lines: list[dict] | None = None) -> dict[str, Any]:
    ctx = require_tenant()
    inv = {
        "id": str(uuid.uuid4()),
        "tenant_id": ctx.tenant_id,
        "account_id": account_id,
        "amount_cents": amount.cents,
        "currency": amount.currency,
        "status": "open",
        "lines": lines or [],
    }
    invoice_repo.upsert(inv)
    return inv


def get_invoice(invoice_id: str) -> dict[str, Any] | None:
    key = cache.cache_key_invoice(invoice_id)
    hit = cache.get(key)
    if hit is not None:
        return hit
    row = invoice_repo.get(invoice_id)
    if row:
        cache.set(key, row)
    return row


def mark_paid(invoice_id: str) -> dict[str, Any]:
    inv = invoice_repo.get(invoice_id)
    if inv is None:
        raise KeyError(invoice_id)
    inv = {**inv, "status": "paid"}
    invoice_repo.upsert(inv)
    cache.invalidate(cache.cache_key_invoice(invoice_id))
    return inv


def serialize_public(inv: dict[str, Any]) -> dict[str, Any]:
    """Public JSON shape for API responses."""
    return {
        "id": inv["id"],
        "tenant_id": inv["tenant_id"],
        "account_id": inv["account_id"],
        "amount_cents": inv["amount_cents"],
        "currency": inv.get("currency", "USD"),
        "status": inv["status"],
    }
''',
    )

    w(
        "service/payment_service.py",
        '''
from __future__ import annotations

from typing import Any
import uuid
import threading

from store import payment_repo, webhook_repo, outbox, invoice_repo
from service import invoice_service, entitlement_service
from pkg import events
from pkg.tenant import require_tenant, set_tenant, TenantContext


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        if key not in _LOCKS:
            _LOCKS[key] = threading.Lock()
        return _LOCKS[key]


def handle_payment_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """Process payment.succeeded webhook.

    Race: check-then-act on webhook_id without holding a lock across side effects.
    """
    webhook_id = payload["webhook_id"]
    tenant_id = payload["tenant_id"]
    invoice_id = payload["invoice_id"]
    amount_cents = int(payload["amount_cents"])

    # early return if already processed — but concurrent callers both pass
    if webhook_repo.is_processed(webhook_id):
        return {"status": "duplicate", "webhook_id": webhook_id}

    set_tenant(TenantContext(tenant_id=tenant_id))
    # side effects before mark — concurrent duplicate can double-apply
    pay = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "invoice_id": invoice_id,
        "amount_cents": amount_cents,
        "webhook_id": webhook_id,
    }
    payment_repo.upsert(pay)
    invoice_service.mark_paid(invoice_id)
    inv = invoice_repo.get(invoice_id) or {}
    account_id = inv.get("account_id") or payload.get("account_id")
    if account_id:
        entitlement_service.activate_paid_plan(account_id, tenant_id)
    outbox.insert(events.invoice_paid(tenant_id, invoice_id, pay["id"]))
    webhook_repo.mark_processed(webhook_id, {"payment_id": pay["id"]})
    return {"status": "ok", "payment_id": pay["id"], "webhook_id": webhook_id}


def record_payment(invoice_id: str, amount_cents: int) -> dict[str, Any]:
    ctx = require_tenant()
    pay = {
        "id": str(uuid.uuid4()),
        "tenant_id": ctx.tenant_id,
        "invoice_id": invoice_id,
        "amount_cents": amount_cents,
    }
    return payment_repo.upsert(pay)
''',
    )

    w(
        "service/entitlement_service.py",
        '''
from __future__ import annotations

from typing import Any

from store import entitlement_repo, outbox
from pkg import events


def activate_paid_plan(account_id: str, tenant_id: str) -> dict[str, Any]:
    row = entitlement_repo.set_plan(account_id, tenant_id, "pro")
    outbox.insert(events.entitlement_changed(tenant_id, account_id, "pro"))
    return row


def get_plan(account_id: str) -> str | None:
    row = entitlement_repo.get(account_id)
    return None if row is None else row.get("plan")
''',
    )

    w(
        "service/ledger_service.py",
        '''
from __future__ import annotations

from typing import Any
import uuid

from store import ledger_repo, outbox
from pkg import events
from pkg.money import Money
from pkg.tenant import require_tenant


def post_entry(amount: Money, memo: str = "") -> dict[str, Any]:
    ctx = require_tenant()
    entry = {
        "id": str(uuid.uuid4()),
        "tenant_id": ctx.tenant_id,
        "cents": amount.cents,
        "currency": amount.currency,
        "memo": memo,
        "source": "live",
    }
    ledger_repo.append(entry)
    outbox.insert(events.ledger_entry_posted(ctx.tenant_id, entry["id"]))
    return entry


def split_credit(total: Money, parts: int) -> list[Money]:
    return total.split(parts)


def list_entries() -> list[dict[str, Any]]:
    ctx = require_tenant()
    return ledger_repo.list_by_tenant(ctx.tenant_id)
''',
    )

    w(
        "service/reconcile_service.py",
        '''
from __future__ import annotations

from typing import Any

from store import invoice_repo, payment_repo
from pkg.tenant import require_tenant


def reconcile_tenant() -> dict[str, Any]:
    """Match open invoices to payments.

    N+1: loads payments per invoice individually.
    """
    ctx = require_tenant()
    invoices = invoice_repo.list_by_tenant(ctx.tenant_id)
    matched = 0
    gaps = []
    for inv in invoices:
        pays = payment_repo.list_by_invoice(inv["id"])  # one query each
        paid = sum(p.get("amount_cents", 0) for p in pays)
        if paid >= inv.get("amount_cents", 0) and inv.get("status") == "paid":
            matched += 1
        elif paid > 0 and inv.get("status") != "paid":
            gaps.append(inv["id"])
    return {"matched": matched, "gaps": gaps, "invoice_count": len(invoices)}


def reconcile_all_tenants(tenant_ids: list[str]) -> list[dict[str, Any]]:
    from pkg.tenant import set_tenant, TenantContext, clear_tenant

    out = []
    for tid in tenant_ids:
        set_tenant(TenantContext(tenant_id=tid))
        out.append({"tenant_id": tid, **reconcile_tenant()})
        clear_tenant()
    return out
''',
    )

    w(
        "service/admin_service.py",
        '''
from __future__ import annotations

from typing import Any

from store import account_repo, invoice_repo, audit_repo
from pkg.tenant import require_tenant


def export_invoices(target_tenant_id: str | None = None) -> list[dict[str, Any]]:
    """Internal export used by ops dashboards.

    Confused deputy: trusts target_tenant_id from the caller without requiring admin
    or matching the caller's tenant.
    """
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
''',
    )

    w(
        "service/legacy/compat.py",
        '''
from __future__ import annotations
# decoy legacy shim

def old_amount_field(row: dict) -> float:
    return float(row.get("amount", 0)) / 100.0
''',
    )

    w(
        "service/migration_service.py",
        '''
from __future__ import annotations

from store.migrations.runner import run_all
from store import ledger_repo


def apply_pending() -> dict:
    applied = run_all()
    leftover = ledger_repo.list_legacy_unmigrated()
    return {"applied": applied, "legacy_remaining": len(leftover)}
''',
    )

    # --- api ---
    w(
        "api/app.py",
        '''
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
''',
    )

    w(
        "api/middleware/auth.py",
        '''
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
''',
    )

    w(
        "api/invoices.py",
        '''
from __future__ import annotations

from service import invoice_service
from pkg.money import Money
from pkg.tenant import require_tenant


def get_one(id: str, body=None, headers=None):
    require_tenant()
    inv = invoice_service.get_invoice(id)
    if inv is None:
        return {"error": "not_found", "status": 404}
    return {"status": 200, "invoice": invoice_service.serialize_public(inv)}


def create(body=None, headers=None, **_):
    require_tenant()
    body = body or {}
    amount = Money(int(body["amount_cents"]), body.get("currency", "USD"))
    inv = invoice_service.create_invoice(body["account_id"], amount, body.get("lines"))
    return {"status": 201, "invoice": invoice_service.serialize_public(inv)}
''',
    )

    w(
        "api/accounts.py",
        '''
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
''',
    )

    w(
        "api/webhooks.py",
        '''
from __future__ import annotations

from service import payment_service


def payment(body=None, headers=None, **_):
    body = body or {}
    return {"status": 200, **payment_service.handle_payment_webhook(body)}
''',
    )

    w(
        "api/admin.py",
        '''
from __future__ import annotations

from pkg.authz import require_admin
from service import admin_service


def export(body=None, headers=None, **_):
    require_admin()
    body = body or {}
    rows = admin_service.export_invoices(body.get("tenant_id"))
    return {"status": 200, "invoices": rows}
''',
    )

    w(
        "api/internal.py",
        '''
from __future__ import annotations

from service import admin_service
from pkg.tenant import require_tenant


def export(body=None, headers=None, **_):
    """Internal path used by sibling services — weaker checks than /v1/admin."""
    require_tenant()
    body = body or {}
    # passes through target tenant from body without admin gate
    rows = admin_service.export_invoices(body.get("tenant_id"))
    return {"status": 200, "invoices": rows}
''',
    )

    # --- worker ---
    w(
        "worker/outbox_worker.py",
        '''
from __future__ import annotations

from typing import Any, Callable

from store import outbox


class PoisonError(RuntimeError):
    pass


def default_publish(event: dict[str, Any]) -> None:
    if event.get("payload", {}).get("poison"):
        raise PoisonError("poison pill")
    # pretend publish succeeded
    return None


def process_once(publish: Callable[[dict], None] | None = None) -> dict[str, Any]:
    """Drain outbox batch.

    BUG: acks even when publish raises (poison pill loses the event forever).
    """
    publish = publish or default_publish
    batch = outbox.claim_batch()
    ok = 0
    failed = 0
    for row in batch:
        try:
            publish(row)
            outbox.mark_published(row["id"])
            ok += 1
        except Exception:
            failed += 1
        # always ack — poison rows disappear
        outbox.ack(row["id"])
    return {"ok": ok, "failed": failed, "batch": len(batch)}
''',
    )

    w(
        "worker/retry.py",
        '''
from __future__ import annotations

def backoff_s(attempt: int) -> float:
    return min(60.0, 0.5 * (2 ** max(0, attempt)))
''',
    )

    w(
        "worker/reconcile_job.py",
        '''
from __future__ import annotations

from service.reconcile_service import reconcile_tenant
from pkg.tenant import set_tenant, TenantContext, clear_tenant


def run_for(tenant_id: str) -> dict:
    set_tenant(TenantContext(tenant_id=tenant_id))
    try:
        return reconcile_tenant()
    finally:
        clear_tenant()
''',
    )

    # --- client (contract drift) ---
    w(
        "client/models.py",
        '''
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class InvoiceDTO:
    id: str
    tenant_id: str
    account_id: str
    amount: float  # BUG: client expects major units float; API sends amount_cents int
    currency: str
    status: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "InvoiceDTO":
        # prefers "amount" major units; falls back incorrectly
        if "amount" in data:
            amount = float(data["amount"])
        elif "amount_cents" in data:
            # wrongly treats cents as dollars
            amount = float(data["amount_cents"])
        else:
            amount = 0.0
        return cls(
            id=data["id"],
            tenant_id=data["tenant_id"],
            account_id=data["account_id"],
            amount=amount,
            currency=data.get("currency", "USD"),
            status=data["status"],
        )
''',
    )

    w(
        "client/http.py",
        '''
from __future__ import annotations

from typing import Any, Callable

from client.models import InvoiceDTO


class LedgerClient:
    """Thin typed client used by sibling services."""

    def __init__(self, handle: Callable[..., dict]):
        self._handle = handle

    def get_invoice(self, invoice_id: str, headers: dict[str, str]) -> InvoiceDTO:
        resp = self._handle("GET", f"/v1/invoices/{invoice_id}", headers=headers)
        if resp.get("status") != 200:
            raise RuntimeError(resp)
        return InvoiceDTO.from_api(resp["invoice"])

    def create_invoice(self, body: dict[str, Any], headers: dict[str, str]) -> InvoiceDTO:
        resp = self._handle("POST", "/v1/invoices", headers=headers, body=body)
        if resp.get("status") != 201:
            raise RuntimeError(resp)
        return InvoiceDTO.from_api(resp["invoice"])
''',
    )

    w(
        "client/__init__.py",
        'from client.http import LedgerClient\nfrom client.models import InvoiceDTO\n',
    )

    # scripts
    w(
        "scripts/seed.py",
        '''
from __future__ import annotations

from store import db, account_repo, invoice_repo
from pkg.tenant import set_tenant, TenantContext, clear_tenant


def seed_demo(n_invoices: int = 50) -> None:
    db.reset()
    for tid in ("t_alpha", "t_beta"):
        set_tenant(TenantContext(tenant_id=tid))
        account_repo.upsert({"id": f"acct_{tid}", "tenant_id": tid, "name": tid})
        for i in range(n_invoices):
            invoice_repo.upsert(
                {
                    "id": f"inv_{tid}_{i}",
                    "tenant_id": tid,
                    "account_id": f"acct_{tid}",
                    "amount_cents": 1000 + i,
                    "currency": "USD",
                    "status": "open" if i % 3 else "paid",
                    "lines": [],
                }
            )
        clear_tenant()


if __name__ == "__main__":
    seed_demo()
    print("seeded")
''',
    )

    # public smoke tests
    w(
        "tests/test_smoke.py",
        '''
from __future__ import annotations

from store import db, cache
from pkg.tenant import set_tenant, TenantContext, clear_tenant
from pkg.money import Money
from service import account_service, invoice_service, ledger_service
from api.app import handle


def setup_function(_=None):
    db.reset()
    cache.reset()
    clear_tenant()


def test_create_account_and_invoice():
    set_tenant(TenantContext(tenant_id="t1"))
    account_service.create_account("a1", "Alpha")
    inv = invoice_service.create_invoice("a1", Money(2500))
    assert inv["amount_cents"] == 2500
    assert inv["tenant_id"] == "t1"


def test_api_health():
    assert handle("GET", "/health")["ok"] is True


def test_money_add():
    assert (Money(10) + Money(5)).cents == 15


def test_ledger_post():
    set_tenant(TenantContext(tenant_id="t1"))
    e = ledger_service.post_entry(Money(100), "memo")
    assert e["cents"] == 100
''',
    )

    w(
        "tests/conftest.py",
        '''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
''',
    )

    # a few more decoy modules for size
    for name in [
        "service/report_service.py",
        "service/usage_service.py",
        "service/credit_service.py",
        "service/plan_catalog.py",
        "store/session_repo.py",
        "store/feature_flag_repo.py",
        "api/usage.py",
        "api/plans.py",
        "worker/usage_rollup.py",
        "pkg/clock.py",
        "pkg/ids.py",
        "config/logging.py",
    ]:
        mod = name.replace("/", ".").removesuffix(".py")
        w(
            name,
            f'''
from __future__ import annotations
"""Decoy module {mod}."""

def ping() -> str:
    return "{mod}"
''',
        )

    n = sum(1 for _ in ROOT.rglob("*.py"))
    print(f"core written; python files now: {n}")


if __name__ == "__main__":
    main()
