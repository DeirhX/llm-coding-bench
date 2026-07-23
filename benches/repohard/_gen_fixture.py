#!/usr/bin/env python3.14
"""Generate ledgerkit fixture scaffolding (run once from repo root)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent / "fixture" / "ledgerkit"


def w(rel: str, body: str) -> None:
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.lstrip("\n") if body.startswith("\n") else body, encoding="utf-8")
    if not body.endswith("\n"):
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")


def main() -> None:
    # packages
    for pkg in [
        "",
        "api",
        "api/middleware",
        "service",
        "service/legacy",
        "store",
        "store/migrations",
        "worker",
        "pkg",
        "client",
        "scripts",
        "tests",
        "config",
        "billing",
        "billing/tax",
        "billing/fx",
        "ops",
        "ops/metrics",
        "ops/health",
    ]:
        w(f"{pkg}/__init__.py" if pkg else "__init__.py", '"""ledgerkit package."""\n')

    w(
        "README.md",
        """# LedgerKit

Multi-tenant billing / ledger service (synthetic fixture for benchmarks).

## Layout

- `api/` — HTTP-ish handlers (in-process, no real server)
- `service/` — domain services
- `store/` — persistence, cache, outbox
- `worker/` — async processors
- `pkg/` — shared primitives (tenant, money, events)
- `client/` — typed client used by sibling services
- `billing/` — tax / FX helpers
- `ops/` — health and metrics

## Invariants (docs)

I1. Webhook side effects are idempotent on `webhook_id`.
I2. Cache entries are tenant-scoped.
I3. Money is integer cents end-to-end.
I4. Migrations leave no unread legacy rows.
I5. Internal admin paths authorize the caller's tenant (or explicit admin role).
I6. Public JSON uses `amount_cents` (int).
I7. Outbox rows are only acked after successful publish.

Run smoke tests: `python -m pytest tests -q` from this directory.
""",
    )

    # --- config ---
    w(
        "config/settings.py",
        '''
from __future__ import annotations

DEFAULT_CURRENCY = "USD"
CACHE_TTL_S = 300
OUTBOX_BATCH = 32
WEBHOOK_MAX_ATTEMPTS = 5
''',
    )

    # --- pkg ---
    w(
        "pkg/tenant.py",
        '''
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
''',
    )

    w(
        "pkg/money.py",
        '''
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
        # BUG intentionally left for money_rounding_split task — uses float.
        share = (self.cents / parts)
        base = int(share)
        out = [Money(base, self.currency) for _ in range(parts)]
        # remainder assigned poorly
        return out


def _same(a: Money, b: Money) -> None:
    if a.currency != b.currency:
        raise ValueError("currency mismatch")


def from_major(amount: float, currency: str = "USD") -> Money:
    return Money(int(round(amount * 100)), currency)
''',
    )

    w(
        "pkg/events.py",
        '''
from __future__ import annotations

from typing import Any
import time
import uuid


def _base(kind: str, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "tenant_id": tenant_id,
        "payload": payload,
        "ts": time.time(),
    }


def invoice_paid(tenant_id: str, invoice_id: str, payment_id: str) -> dict[str, Any]:
    return _base("InvoicePaid", tenant_id, {"invoice_id": invoice_id, "payment_id": payment_id})


def entitlement_changed(tenant_id: str, account_id: str, plan: str) -> dict[str, Any]:
    return _base("EntitlementChanged", tenant_id, {"account_id": account_id, "plan": plan})


def ledger_entry_posted(tenant_id: str, entry_id: str) -> dict[str, Any]:
    return _base("LedgerEntryPosted", tenant_id, {"entry_id": entry_id})
''',
    )

    w(
        "pkg/idempotency.py",
        '''
from __future__ import annotations

from store import db


def seen(scope: str, key: str) -> bool:
    return db.get(f"idemp:{scope}", key) is not None


def remember(scope: str, key: str) -> None:
    db.put(f"idemp:{scope}", key, {"ok": True})
''',
    )

    w(
        "pkg/authz.py",
        '''
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
''',
    )

    w(
        "pkg/errors.py",
        '''
class NotFound(KeyError):
    pass


class Conflict(RuntimeError):
    pass


class ValidationError(ValueError):
    pass
''',
    )

    # --- store ---
    w(
        "store/db.py",
        '''
from __future__ import annotations

from typing import Any

_TABLES: dict[str, dict[str, Any]] = {}


def reset() -> None:
    _TABLES.clear()


def table(name: str) -> dict[str, Any]:
    return _TABLES.setdefault(name, {})


def put(name: str, key: str, value: Any) -> None:
    table(name)[key] = value


def get(name: str, key: str) -> Any | None:
    return table(name).get(key)


def delete(name: str, key: str) -> None:
    table(name).pop(key, None)


def list_all(name: str) -> list[Any]:
    return list(table(name).values())


def list_where(name: str, **preds: Any) -> list[Any]:
    rows = []
    for row in list_all(name):
        if all(row.get(k) == v for k, v in preds.items()):
            rows.append(row)
    return rows
''',
    )

    w(
        "store/cache.py",
        '''
from __future__ import annotations

from typing import Any

_CACHE: dict[str, Any] = {}


def reset() -> None:
    _CACHE.clear()


def cache_key_account(account_id: str) -> str:
    # BUG for tenant_cache_key_collision: tenant omitted
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

    w(
        "store/outbox.py",
        '''
from __future__ import annotations

from typing import Any
import uuid

from store import db


def insert(event: dict[str, Any]) -> str:
    eid = event.get("id") or str(uuid.uuid4())
    row = {**event, "id": eid, "acked": False, "published": False}
    db.put("outbox", eid, row)
    return eid


def claim_batch(limit: int = 32) -> list[dict[str, Any]]:
    rows = [r for r in db.list_all("outbox") if not r.get("acked")]
    return rows[:limit]


def ack(row_id: str) -> None:
    row = db.get("outbox", row_id)
    if row:
        row = {**row, "acked": True}
        db.put("outbox", row_id, row)


def mark_published(row_id: str) -> None:
    row = db.get("outbox", row_id)
    if row:
        row = {**row, "published": True}
        db.put("outbox", row_id, row)
''',
    )

    # stub repos + many files for size
    for name, body in [
        (
            "store/account_repo.py",
            '''
from __future__ import annotations
from typing import Any
from store import db

def upsert(account: dict[str, Any]) -> dict[str, Any]:
    db.put("accounts", account["id"], account)
    return account

def get(account_id: str) -> dict[str, Any] | None:
    return db.get("accounts", account_id)

def list_by_tenant(tenant_id: str) -> list[dict[str, Any]]:
    return db.list_where("accounts", tenant_id=tenant_id)
''',
        ),
        (
            "store/invoice_repo.py",
            '''
from __future__ import annotations
from typing import Any
from store import db

def upsert(inv: dict[str, Any]) -> dict[str, Any]:
    db.put("invoices", inv["id"], inv)
    return inv

def get(invoice_id: str) -> dict[str, Any] | None:
    return db.get("invoices", invoice_id)

def list_by_tenant(tenant_id: str) -> list[dict[str, Any]]:
    return db.list_where("invoices", tenant_id=tenant_id)

def list_unpaid(tenant_id: str) -> list[dict[str, Any]]:
    return [r for r in list_by_tenant(tenant_id) if r.get("status") != "paid"]

def export_all() -> list[dict[str, Any]]:
    return db.list_all("invoices")
''',
        ),
        (
            "store/payment_repo.py",
            '''
from __future__ import annotations
from typing import Any
from store import db

def upsert(p: dict[str, Any]) -> dict[str, Any]:
    db.put("payments", p["id"], p)
    return p

def get(payment_id: str) -> dict[str, Any] | None:
    return db.get("payments", payment_id)

def list_by_invoice(invoice_id: str) -> list[dict[str, Any]]:
    return db.list_where("payments", invoice_id=invoice_id)
''',
        ),
        (
            "store/ledger_repo.py",
            '''
from __future__ import annotations
from typing import Any
from store import db

def append(entry: dict[str, Any]) -> dict[str, Any]:
    db.put("ledger", entry["id"], entry)
    return entry

def get(entry_id: str) -> dict[str, Any] | None:
    return db.get("ledger", entry_id)

def list_by_tenant(tenant_id: str) -> list[dict[str, Any]]:
    return db.list_where("ledger", tenant_id=tenant_id)

def list_legacy_unmigrated() -> list[dict[str, Any]]:
    return [r for r in db.list_all("ledger_legacy") if not r.get("migrated")]
''',
        ),
        (
            "store/webhook_repo.py",
            '''
from __future__ import annotations
from typing import Any
from store import db

def mark_processed(webhook_id: str, meta: dict[str, Any] | None = None) -> None:
    db.put("processed_webhooks", webhook_id, meta or {"id": webhook_id})

def is_processed(webhook_id: str) -> bool:
    return db.get("processed_webhooks", webhook_id) is not None
''',
        ),
        (
            "store/entitlement_repo.py",
            '''
from __future__ import annotations
from typing import Any
from store import db

def set_plan(account_id: str, tenant_id: str, plan: str) -> dict[str, Any]:
    row = {"account_id": account_id, "tenant_id": tenant_id, "plan": plan}
    db.put("entitlements", account_id, row)
    return row

def get(account_id: str) -> dict[str, Any] | None:
    return db.get("entitlements", account_id)
''',
        ),
        (
            "store/audit_repo.py",
            '''
from __future__ import annotations
from typing import Any
import uuid
from store import db

def write(tenant_id: str, action: str, detail: dict[str, Any]) -> str:
    eid = str(uuid.uuid4())
    db.put("audit", eid, {"id": eid, "tenant_id": tenant_id, "action": action, "detail": detail})
    return eid
''',
        ),
    ]:
        w(name, body)

    # migration helpers
    w(
        "store/migrations/__init__.py",
        '"""DB migrations."""\n',
    )
    w(
        "store/migrations/registry.py",
        '''
from __future__ import annotations

from store.migrations import m001_init, m002_backfill_ledger

MIGRATIONS = [
    ("001_init", m001_init.apply),
    ("002_backfill_ledger", m002_backfill_ledger.apply),
]
''',
    )
    w(
        "store/migrations/m001_init.py",
        '''
from __future__ import annotations
from store import db

def apply() -> None:
    db.put("schema_meta", "001_init", {"applied": True})
''',
    )
    w(
        "store/migrations/m002_backfill_ledger.py",
        '''
from __future__ import annotations
from store import db

def apply() -> None:
    """Backfill legacy ledger rows into ledger table.

    Marks migration applied even if legacy rows remain — hole for migration_backfill_hole.
    """
    legacy = db.list_all("ledger_legacy")
    for row in legacy:
        if row.get("migrated"):
            continue
        # only migrate rows with explicit flag (misses the rest)
        if row.get("priority") == "high":
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
''',
    )
    w(
        "store/migrations/runner.py",
        '''
from __future__ import annotations
from store import db
from store.migrations.registry import MIGRATIONS

def run_all() -> list[str]:
    done = []
    for name, fn in MIGRATIONS:
        if db.get("schema_meta", name):
            continue
        fn()
        done.append(name)
    return done
''',
    )

    # pad with many small modules
    for i in range(1, 21):
        w(
            f"billing/tax/rule_{i:02d}.py",
            f'''
from __future__ import annotations

RULE_ID = "{i:02d}"

def rate_for(region: str) -> float:
    return 0.0 if region == "XX" else 0.01 * ({i} % 7)
''',
        )
    for i in range(1, 11):
        w(
            f"billing/fx/pair_{i:02d}.py",
            f'''
from __future__ import annotations

PAIR = "USD_X{i}"

def convert(cents: int) -> int:
    return cents  # identity stub
''',
        )
    for i in range(1, 16):
        w(
            f"ops/metrics/metric_{i:02d}.py",
            f'''
from __future__ import annotations

NAME = "ledgerkit.metric_{i:02d}"

def observe(value: float) -> None:
    return None
''',
        )

    w(
        "ops/health/checks.py",
        '''
from __future__ import annotations
from store import db

def ok() -> dict:
    return {"db": True, "tables": len(db._TABLES)}
''',
    )
    w(
        "billing/tax/engine.py",
        '''
from __future__ import annotations
from billing.tax import rule_01

def tax_cents(cents: int, region: str) -> int:
    r = rule_01.rate_for(region)
    return int(cents * r)
''',
    )
    w(
        "billing/fx/engine.py",
        '''
from __future__ import annotations
from billing.fx import pair_01

def to_usd(cents: int, currency: str) -> int:
    if currency == "USD":
        return cents
    return pair_01.convert(cents)
''',
    )

    print(f"wrote scaffold under {ROOT}")
    n = sum(1 for _ in ROOT.rglob("*.py"))
    print(f"python files: {n}")


if __name__ == "__main__":
    main()
