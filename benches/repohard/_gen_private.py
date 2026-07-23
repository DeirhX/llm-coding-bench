#!/usr/bin/env python3.14
"""Generate assignments, private tests, and gold patches for repohard."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parent
ASSIGN = ROOT / "assignment"
PRIVATE = ROOT / "private"
TESTS = PRIVATE / "tests"
GOLD = PRIVATE / "gold"
FIXTURE = ROOT / "fixture" / "ledgerkit"


def w(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dedent(body).lstrip("\n")
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


TASKS = [
    (
        "race_webhook_idempotency",
        "Payment webhook double-applies under concurrency",
        """
# Payment webhook double-applies under concurrency

Ops reports that when the payment provider retries a `payment.succeeded`
webhook at the same time as the original delivery, **two payments** and
**two entitlement activations** appear for a single `webhook_id`.

## Expected behavior

`service.payment_service.handle_payment_webhook` must be idempotent on
`webhook_id`: concurrent callers with the same id produce exactly one
payment row and one entitlement activation.

## Constraints

- Fix the production code under `service/` / `store/` as needed.
- Do not weaken webhook processing (still mark paid + emit outbox on first success).
- Deliver a unified diff of your changes.
""",
    ),
    (
        "tenant_cache_key_collision",
        "Account cache leaks across tenants",
        """
# Account cache leaks across tenants

Two tenants can create accounts with the same `account_id` (ids are only
unique within a tenant). After tenant A loads an account, tenant B sometimes
sees A's account data from cache.

## Expected behavior

Cached account reads must be tenant-scoped. A cache fill by tenant A must
never be returned to tenant B for the same account id.

## Constraints

- Inspect `store/cache.py` and `service/account_service.py`.
- Fix without removing caching entirely.
""",
    ),
    (
        "money_rounding_split",
        "Money.split loses cents",
        """
# Money.split loses cents

`pkg.money.Money.split(parts)` is used when splitting credits across line
items. Finance noticed that split parts **do not sum** back to the original
cent amount (money disappears).

## Expected behavior

For any positive `parts`, `sum(p.cents for p in money.split(parts)) == money.cents`.
Parts may differ by at most 1 cent (remainder distribution).

## Constraints

- Keep integer cents (no floats in the final arithmetic).
- Preserve currency on each part.
""",
    ),
    (
        "migration_backfill_hole",
        "Ledger backfill leaves legacy rows",
        """
# Ledger backfill leaves legacy rows

Migration `002_backfill_ledger` is marked applied, but many rows remain in
`ledger_legacy` with `migrated=False`. Reconciliation still misses those
amounts.

## Expected behavior

Running the migration must move **all** unmigrated legacy ledger rows into
`ledger` and mark them migrated. `schema_meta` may still record the migration.

## Constraints

- Fix `store/migrations/m002_backfill_ledger.py` (and helpers if needed).
- Do not drop legacy table.
""",
    ),
    (
        "nplus1_reconciliation",
        "Reconciliation N+1 payment lookups",
        """
# Reconciliation N+1 payment lookups

`service.reconcile_service.reconcile_tenant` is too slow on tenants with
hundreds of invoices. Profiling shows one `payment_repo.list_by_invoice`
call per invoice.

## Expected behavior

Reconcile a tenant with many invoices using **O(1)** calls to load payments
for the whole tenant (or a single bulk helper), not one call per invoice.
Functional matching results must remain correct.

`store.payment_repo` exposes `list_by_invoice` and may be extended with a
bulk helper. A call counter on `list_by_invoice` is used in grading — keep
per-invoice calls near zero for large sets.
""",
    ),
    (
        "confused_deputy_admin",
        "Internal export ignores tenant boundary",
        """
# Internal export ignores tenant boundary

`GET /internal/export` (via `api.internal.export` →
`service.admin_service.export_invoices`) lets a non-admin caller pass
`tenant_id` in the body and dump another tenant's invoices.

## Expected behavior

Non-admin callers may only export their own tenant. Exporting another
tenant requires `is_admin`. Admin export of any tenant remains allowed.

## Constraints

- Fix authorization in service and/or API layer.
- Do not remove the internal route.
""",
    ),
    (
        "client_contract_drift",
        "Client misreads invoice amounts",
        """
# Client misreads invoice amounts

Sibling services using `client.LedgerClient` / `InvoiceDTO` show invoice
amounts 100× too large. The public API serializes `amount_cents` (int).
The client treats that field as major-unit dollars.

## Expected behavior

`InvoiceDTO.from_api` must interpret `amount_cents` as integer cents and
expose `amount` as major units (`cents / 100.0`). If both `amount` and
`amount_cents` are present, prefer the cents field for consistency with
the API.

## Constraints

- Fix the client package; API shape (`amount_cents`) stays.
""",
    ),
    (
        "outbox_poison_retry",
        "Outbox acks poison events",
        """
# Outbox acks poison events

`worker.outbox_worker.process_once` acknowledges outbox rows even when
`publish` raises. Poison payloads disappear and are never retried.

## Expected behavior

Only ack (and mark published) after a successful publish. Failed publishes
must leave the row unacked for a later retry.

## Constraints

- Keep batch claiming behavior.
- Do not swallow all errors silently without leaving the row retryable.
""",
    ),
]


PRIVATE_TESTS = {
    "race_webhook_idempotency": '''
from __future__ import annotations

import threading
from store import db, cache, payment_repo, entitlement_repo, invoice_repo, account_repo
from service.payment_service import handle_payment_webhook


def setup_function(_=None):
    db.reset()
    cache.reset()
    account_repo.upsert({"id": "a1", "tenant_id": "t1", "name": "A"})
    invoice_repo.upsert(
        {
            "id": "inv1",
            "tenant_id": "t1",
            "account_id": "a1",
            "amount_cents": 500,
            "currency": "USD",
            "status": "open",
            "lines": [],
        }
    )


def test_concurrent_same_webhook_single_payment():
    payload = {
        "webhook_id": "wh-1",
        "tenant_id": "t1",
        "invoice_id": "inv1",
        "account_id": "a1",
        "amount_cents": 500,
    }
    barriers = threading.Barrier(8)
    errors: list[BaseException] = []

    def run():
        try:
            barriers.wait(timeout=5)
            handle_payment_webhook(payload)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors, errors
    pays = payment_repo.list_by_invoice("inv1")
    assert len(pays) == 1, pays
    ent = entitlement_repo.get("a1")
    assert ent is not None and ent["plan"] == "pro"
    inv = invoice_repo.get("inv1")
    assert inv["status"] == "paid"


def test_distinct_webhooks_still_work():
    # second invoice
    invoice_repo.upsert(
        {
            "id": "inv2",
            "tenant_id": "t1",
            "account_id": "a1",
            "amount_cents": 100,
            "currency": "USD",
            "status": "open",
            "lines": [],
        }
    )
    handle_payment_webhook(
        {
            "webhook_id": "wh-a",
            "tenant_id": "t1",
            "invoice_id": "inv1",
            "account_id": "a1",
            "amount_cents": 500,
        }
    )
    # reset invoice1 already paid — use inv2
    handle_payment_webhook(
        {
            "webhook_id": "wh-b",
            "tenant_id": "t1",
            "invoice_id": "inv2",
            "account_id": "a1",
            "amount_cents": 100,
        }
    )
    assert len(payment_repo.list_by_invoice("inv1")) == 1
    assert len(payment_repo.list_by_invoice("inv2")) == 1
''',
    "tenant_cache_key_collision": '''
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
''',
    "money_rounding_split": '''
from __future__ import annotations

from pkg.money import Money


def test_split_sums_exactly():
    for cents, parts in [(100, 3), (1, 3), (999, 7), (50, 1), (10, 4)]:
        m = Money(cents)
        chunks = m.split(parts)
        assert len(chunks) == parts
        assert sum(x.cents for x in chunks) == cents
        assert all(x.currency == "USD" for x in chunks)
        assert max(x.cents for x in chunks) - min(x.cents for x in chunks) <= 1


def test_split_rejects_bad_parts():
    try:
        Money(10).split(0)
        assert False, "expected ValueError"
    except ValueError:
        pass
''',
    "migration_backfill_hole": '''
from __future__ import annotations

from store import db, ledger_repo
from store.migrations.m002_backfill_ledger import apply
from service.migration_service import apply_pending


def setup_function(_=None):
    db.reset()
    for i, pri in enumerate(["high", "low", "low", None, "high"]):
        db.put(
            "ledger_legacy",
            f"L{i}",
            {
                "id": f"L{i}",
                "tenant_id": "t1",
                "cents": 100 * (i + 1),
                "currency": "USD",
                "priority": pri,
                "migrated": False,
            },
        )


def test_all_legacy_migrated():
    apply()
    left = ledger_repo.list_legacy_unmigrated()
    assert left == [], left
    ledgers = db.list_all("ledger")
    assert len(ledgers) == 5
    assert sum(r["cents"] for r in ledgers) == 100 + 200 + 300 + 400 + 500


def test_migration_service_reports_zero_remaining():
    result = apply_pending()
    assert result["legacy_remaining"] == 0
''',
    "nplus1_reconciliation": '''
from __future__ import annotations

from store import db, cache, invoice_repo, payment_repo, account_repo
from pkg.tenant import set_tenant, TenantContext, clear_tenant
from service.reconcile_service import reconcile_tenant


def setup_function(_=None):
    db.reset()
    cache.reset()
    clear_tenant()
    payment_repo.reset_stats()
    account_repo.upsert({"id": "a1", "tenant_id": "t1", "name": "A"})
    for i in range(40):
        invoice_repo.upsert(
            {
                "id": f"inv{i}",
                "tenant_id": "t1",
                "account_id": "a1",
                "amount_cents": 100,
                "currency": "USD",
                "status": "paid" if i % 2 == 0 else "open",
                "lines": [],
            }
        )
        if i % 2 == 0:
            payment_repo.upsert(
                {
                    "id": f"pay{i}",
                    "tenant_id": "t1",
                    "invoice_id": f"inv{i}",
                    "amount_cents": 100,
                }
            )


def test_reconcile_avoids_per_invoice_payment_lookups():
    set_tenant(TenantContext(tenant_id="t1"))
    result = reconcile_tenant()
    assert result["invoice_count"] == 40
    assert result["matched"] == 20
    # allow a tiny constant number of list_by_invoice calls, not O(n)
    assert payment_repo.stats()["list_by_invoice"] <= 2, payment_repo.stats()


def test_reconcile_gap_detection():
    payment_repo.upsert(
        {"id": "payX", "tenant_id": "t1", "invoice_id": "inv1", "amount_cents": 50}
    )
    set_tenant(TenantContext(tenant_id="t1"))
    result = reconcile_tenant()
    assert "inv1" in result["gaps"]
''',
    "confused_deputy_admin": '''
from __future__ import annotations

from store import db, cache, invoice_repo, account_repo
from pkg.tenant import set_tenant, TenantContext, clear_tenant
from service import admin_service
from api.app import handle


def setup_function(_=None):
    db.reset()
    cache.reset()
    clear_tenant()
    for tid in ("t_victim", "t_attacker"):
        account_repo.upsert({"id": f"a_{tid}", "tenant_id": tid, "name": tid})
        invoice_repo.upsert(
            {
                "id": f"inv_{tid}",
                "tenant_id": tid,
                "account_id": f"a_{tid}",
                "amount_cents": 999,
                "currency": "USD",
                "status": "open",
                "lines": [],
            }
        )


def test_non_admin_cannot_export_other_tenant():
    set_tenant(TenantContext(tenant_id="t_attacker", is_admin=False))
    try:
        admin_service.export_invoices("t_victim")
        assert False, "expected PermissionError"
    except PermissionError:
        pass


def test_non_admin_can_export_own():
    set_tenant(TenantContext(tenant_id="t_attacker", is_admin=False))
    rows = admin_service.export_invoices("t_attacker")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "t_attacker"


def test_admin_can_export_any():
    set_tenant(TenantContext(tenant_id="t_attacker", is_admin=True))
    rows = admin_service.export_invoices("t_victim")
    assert len(rows) == 1
    assert rows[0]["tenant_id"] == "t_victim"


def test_internal_route_blocks_cross_tenant():
    resp = handle(
        "GET",
        "/internal/export",
        headers={"X-Tenant-Id": "t_attacker"},
        body={"tenant_id": "t_victim"},
    )
    assert resp.get("status") in (403, None) or "error" in resp or resp.get("status") == 403
    # also accept raised path turned into error by handle — if handle doesn't catch, call service
    if resp.get("status") == 200:
        assert all(r["tenant_id"] != "t_victim" for r in resp.get("invoices", []))
''',
    "client_contract_drift": '''
from __future__ import annotations

from store import db, cache
from pkg.tenant import set_tenant, TenantContext, clear_tenant
from service import account_service, invoice_service
from pkg.money import Money
from client.models import InvoiceDTO
from client.http import LedgerClient
from api.app import handle


def setup_function(_=None):
    db.reset()
    cache.reset()
    clear_tenant()


def test_from_api_uses_cents():
    dto = InvoiceDTO.from_api(
        {
            "id": "i1",
            "tenant_id": "t1",
            "account_id": "a1",
            "amount_cents": 2599,
            "currency": "USD",
            "status": "open",
        }
    )
    assert abs(dto.amount - 25.99) < 1e-9


def test_client_roundtrip():
    set_tenant(TenantContext(tenant_id="t1"))
    account_service.create_account("a1", "A")
    inv = invoice_service.create_invoice("a1", Money(1234))
    clear_tenant()
    client = LedgerClient(handle)
    dto = client.get_invoice(inv["id"], headers={"X-Tenant-Id": "t1"})
    assert abs(dto.amount - 12.34) < 1e-9
''',
    "outbox_poison_retry": '''
from __future__ import annotations

from store import db, cache, outbox
from worker.outbox_worker import process_once, PoisonError


def setup_function(_=None):
    db.reset()
    cache.reset()


def test_poison_not_acked():
    eid = outbox.insert(
        {
            "id": "e-poison",
            "kind": "X",
            "tenant_id": "t1",
            "payload": {"poison": True},
        }
    )
    result = process_once()
    assert result["failed"] == 1
    row = db.get("outbox", eid)
    assert row is not None
    assert row.get("acked") is False
    assert row.get("published") is not True


def test_success_acked():
    eid = outbox.insert(
        {
            "id": "e-ok",
            "kind": "X",
            "tenant_id": "t1",
            "payload": {"poison": False},
        }
    )
    result = process_once()
    assert result["ok"] == 1
    row = db.get("outbox", eid)
    assert row["acked"] is True
    assert row["published"] is True


def test_retry_after_poison_then_fix():
    eid = outbox.insert(
        {
            "id": "e-flaky",
            "kind": "X",
            "tenant_id": "t1",
            "payload": {"poison": True},
        }
    )
    process_once()
    row = db.get("outbox", eid)
    row["payload"] = {"poison": False}
    db.put("outbox", eid, row)
    process_once()
    row2 = db.get("outbox", eid)
    assert row2["acked"] is True
''',
}


# Gold patches as full-file replacements via unified diff generated at runtime
GOLD_SOURCES: dict[str, dict[str, str]] = {}


def _read(rel: str) -> str:
    return (FIXTURE / rel).read_text(encoding="utf-8")


def build_gold_sources() -> None:
    # race
    GOLD_SOURCES["race_webhook_idempotency"] = {
        "service/payment_service.py": '''\
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
    """Process payment.succeeded webhook (idempotent on webhook_id)."""
    webhook_id = payload["webhook_id"]
    tenant_id = payload["tenant_id"]
    invoice_id = payload["invoice_id"]
    amount_cents = int(payload["amount_cents"])

    lock = _lock_for(webhook_id)
    with lock:
        if webhook_repo.is_processed(webhook_id):
            return {"status": "duplicate", "webhook_id": webhook_id}

        set_tenant(TenantContext(tenant_id=tenant_id))
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
'''
    }

    GOLD_SOURCES["tenant_cache_key_collision"] = {
        "store/cache.py": '''\
from __future__ import annotations

from typing import Any

_CACHE: dict[str, Any] = {}


def reset() -> None:
    _CACHE.clear()


def cache_key_account(tenant_id: str, account_id: str) -> str:
    return f"acct:{tenant_id}:{account_id}"


def cache_key_invoice(invoice_id: str) -> str:
    return f"inv:{invoice_id}"


def get(key: str) -> Any | None:
    return _CACHE.get(key)


def set(key: str, value: Any) -> None:
    _CACHE[key] = value


def invalidate(key: str) -> None:
    _CACHE.pop(key, None)
''',
        "service/account_service.py": '''\
from __future__ import annotations

from typing import Any

from store import account_repo, cache
from pkg.tenant import require_tenant


def get_account(account_id: str) -> dict[str, Any] | None:
    ctx = require_tenant()
    key = cache.cache_key_account(ctx.tenant_id, account_id)
    hit = cache.get(key)
    if hit is not None:
        if hit.get("tenant_id") != ctx.tenant_id:
            cache.invalidate(key)
        else:
            return hit
    row = account_repo.get(account_id, tenant_id=ctx.tenant_id)
    if row is None:
        return None
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
    }

    GOLD_SOURCES["money_rounding_split"] = {
        "pkg/money.py": '''\
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Money:
    cents: int
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not isinstance(self.cents, int):
            raise TypeError("cents must be int")

    def __add__(self, other: "Money") -> "Money":
        _same(self, other)
        return Money(self.cents + other.cents, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        _same(self, other)
        return Money(self.cents - other.cents, self.currency)

    def split(self, parts: int) -> list["Money"]:
        """Split into N parts that sum exactly to self.cents."""
        if parts <= 0:
            raise ValueError("parts must be positive")
        base, rem = divmod(self.cents, parts)
        out: list[Money] = []
        for i in range(parts):
            extra = 1 if i < rem else 0
            out.append(Money(base + extra, self.currency))
        return out


def _same(a: Money, b: Money) -> None:
    if a.currency != b.currency:
        raise ValueError("currency mismatch")


def from_major(amount: float, currency: str = "USD") -> Money:
    return Money(int(round(amount * 100)), currency)
'''
    }

    GOLD_SOURCES["migration_backfill_hole"] = {
        "store/migrations/m002_backfill_ledger.py": '''\
from __future__ import annotations
from store import db

def apply() -> None:
    """Backfill all legacy ledger rows into ledger table."""
    legacy = db.list_all("ledger_legacy")
    for row in legacy:
        if row.get("migrated"):
            continue
        new = {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "cents": row["cents"],
            "currency": row.get("currency", "USD"),
            "source": "legacy",
        }
        db.put("ledger", new["id"], new)
        row = {**row, "migrated": True}
        db.put("ledger_legacy", row["id"], row)
    db.put("schema_meta", "002_backfill_ledger", {"applied": True})
'''
    }

    # payment_repo needs stats + bulk list for nplus1 — plant stats in fixture first
    GOLD_SOURCES["nplus1_reconciliation"] = {
        "store/payment_repo.py": '''\
from __future__ import annotations
from typing import Any
from store import db

_STATS = {"list_by_invoice": 0}


def reset_stats() -> None:
    _STATS["list_by_invoice"] = 0


def stats() -> dict[str, int]:
    return dict(_STATS)


def upsert(p: dict[str, Any]) -> dict[str, Any]:
    db.put("payments", p["id"], p)
    return p

def get(payment_id: str) -> dict[str, Any] | None:
    return db.get("payments", payment_id)

def list_by_invoice(invoice_id: str) -> list[dict[str, Any]]:
    _STATS["list_by_invoice"] += 1
    return db.list_where("payments", invoice_id=invoice_id)


def list_by_tenant(tenant_id: str) -> list[dict[str, Any]]:
    return db.list_where("payments", tenant_id=tenant_id)
''',
        "service/reconcile_service.py": '''\
from __future__ import annotations

from typing import Any
from collections import defaultdict

from store import invoice_repo, payment_repo
from pkg.tenant import require_tenant


def reconcile_tenant() -> dict[str, Any]:
    """Match open invoices to payments (bulk payment load)."""
    ctx = require_tenant()
    invoices = invoice_repo.list_by_tenant(ctx.tenant_id)
    pays = payment_repo.list_by_tenant(ctx.tenant_id)
    by_inv: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in pays:
        by_inv[p["invoice_id"]].append(p)
    matched = 0
    gaps = []
    for inv in invoices:
        paid = sum(p.get("amount_cents", 0) for p in by_inv.get(inv["id"], []))
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
'''
    }

    GOLD_SOURCES["confused_deputy_admin"] = {
        "service/admin_service.py": '''\
from __future__ import annotations

from typing import Any

from store import account_repo, invoice_repo, audit_repo
from pkg.tenant import require_tenant


def export_invoices(target_tenant_id: str | None = None) -> list[dict[str, Any]]:
    """Internal export used by ops dashboards."""
    ctx = require_tenant()
    tid = target_tenant_id or ctx.tenant_id
    if tid != ctx.tenant_id and not ctx.is_admin:
        raise PermissionError("cannot export foreign tenant")
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
        "api/internal.py": '''\
from __future__ import annotations

from service import admin_service
from pkg.tenant import require_tenant


def export(body=None, headers=None, **_):
    """Internal path used by sibling services."""
    require_tenant()
    body = body or {}
    try:
        rows = admin_service.export_invoices(body.get("tenant_id"))
    except PermissionError as e:
        return {"error": str(e), "status": 403}
    return {"status": 200, "invoices": rows}
'''
    }

    GOLD_SOURCES["client_contract_drift"] = {
        "client/models.py": '''\
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class InvoiceDTO:
    id: str
    tenant_id: str
    account_id: str
    amount: float
    currency: str
    status: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "InvoiceDTO":
        if "amount_cents" in data:
            amount = int(data["amount_cents"]) / 100.0
        elif "amount" in data:
            amount = float(data["amount"])
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
'''
    }

    GOLD_SOURCES["outbox_poison_retry"] = {
        "worker/outbox_worker.py": '''\
from __future__ import annotations

from typing import Any, Callable

from store import outbox


class PoisonError(RuntimeError):
    pass


def default_publish(event: dict[str, Any]) -> None:
    if event.get("payload", {}).get("poison"):
        raise PoisonError("poison pill")
    return None


def process_once(publish: Callable[[dict], None] | None = None) -> dict[str, Any]:
    """Drain outbox batch; only ack after successful publish."""
    publish = publish or default_publish
    batch = outbox.claim_batch()
    ok = 0
    failed = 0
    for row in batch:
        try:
            publish(row)
            outbox.mark_published(row["id"])
            outbox.ack(row["id"])
            ok += 1
        except Exception:
            failed += 1
    return {"ok": ok, "failed": failed, "batch": len(batch)}
'''
    }


def make_unified_diff(rel: str, old: str, new: str) -> str:
    import difflib

    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    if old_lines and not old_lines[-1].endswith("\n"):
        old_lines[-1] += "\n"
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
        lineterm="\n",
    )
    return "".join(diff)


def main() -> None:
    for tid, title, body in TASKS:
        w(ASSIGN / f"{tid}.md", f"# {title}\n\n" + body if not body.strip().startswith("#") else body)

    for tid, src in PRIVATE_TESTS.items():
        w(TESTS / tid / "test_grade.py", src)
        w(
            TESTS / tid / "conftest.py",
            '''
import sys
from pathlib import Path

# PYTHONPATH should already point at ledgerkit copy; keep helper noop
''',
        )

    # Ensure buggy fixture payment_repo has stats hooks (needed for grading even unpatched)
    w(
        FIXTURE / "store" / "payment_repo.py",
        '''
from __future__ import annotations
from typing import Any
from store import db

_STATS = {"list_by_invoice": 0}


def reset_stats() -> None:
    _STATS["list_by_invoice"] = 0


def stats() -> dict[str, int]:
    return dict(_STATS)


def upsert(p: dict[str, Any]) -> dict[str, Any]:
    db.put("payments", p["id"], p)
    return p

def get(payment_id: str) -> dict[str, Any] | None:
    return db.get("payments", payment_id)

def list_by_invoice(invoice_id: str) -> list[dict[str, Any]]:
    _STATS["list_by_invoice"] += 1
    return db.list_where("payments", invoice_id=invoice_id)


def list_by_tenant(tenant_id: str) -> list[dict[str, Any]]:
    return db.list_where("payments", tenant_id=tenant_id)
''',
    )

    # Tenant-aware account storage; cache key bug remains in cache/account_service
    w(
        FIXTURE / "store" / "account_repo.py",
        '''
from __future__ import annotations
from typing import Any
from store import db

def _key(tenant_id: str, account_id: str) -> str:
    return f"{tenant_id}:{account_id}"


def upsert(account: dict[str, Any]) -> dict[str, Any]:
    db.put("accounts", _key(account["tenant_id"], account["id"]), account)
    return account

def get(account_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
    if tenant_id is not None:
        return db.get("accounts", _key(tenant_id, account_id))
    # legacy bare-id lookup (ambiguous)
    for row in db.list_all("accounts"):
        if row.get("id") == account_id:
            return row
    return None

def list_by_tenant(tenant_id: str) -> list[dict[str, Any]]:
    return db.list_where("accounts", tenant_id=tenant_id)
''',
    )

    w(
        FIXTURE / "service" / "account_service.py",
        '''
from __future__ import annotations

from typing import Any

from store import account_repo, cache
from pkg.tenant import require_tenant


def get_account(account_id: str) -> dict[str, Any] | None:
    ctx = require_tenant()
    # cache key omits tenant — cross-tenant leak
    key = cache.cache_key_account(account_id)
    hit = cache.get(key)
    if hit is not None:
        return hit
    row = account_repo.get(account_id, tenant_id=ctx.tenant_id)
    if row is None:
        return None
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
        FIXTURE / "store" / "cache.py",
        '''
from __future__ import annotations

from typing import Any

_CACHE: dict[str, Any] = {}


def reset() -> None:
    _CACHE.clear()


def cache_key_account(account_id: str) -> str:
    return f"acct:{account_id}"


def cache_key_invoice(invoice_id: str) -> str:
    return f"inv:{invoice_id}"


def get(key: str) -> Any | None:
    return _CACHE.get(key)


def set(key: str, value: Any) -> None:
    _CACHE[key] = value


def invalidate(key: str) -> None:
    _CACHE.pop(key, None)
''',
    )

    build_gold_sources()
    for tid, files in GOLD_SOURCES.items():
        parts = []
        for rel, new in files.items():
            old = _read(rel)
            # for files we just rewrote above, re-read
            old = (FIXTURE / rel).read_text(encoding="utf-8")
            parts.append(make_unified_diff(rel, old, new))
        patch = "".join(parts)
        w(GOLD / f"{tid}.patch", patch)

    print(f"assignments: {len(list(ASSIGN.glob('*.md')))}")
    print(f"private suites: {len(list(TESTS.iterdir()))}")
    print(f"gold patches: {len(list(GOLD.glob('*.patch')))}")


if __name__ == "__main__":
    main()
