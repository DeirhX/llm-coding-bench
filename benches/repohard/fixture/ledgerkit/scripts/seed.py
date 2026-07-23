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
