from __future__ import annotations

from store.migrations.runner import run_all
from store import ledger_repo


def apply_pending() -> dict:
    applied = run_all()
    leftover = ledger_repo.list_legacy_unmigrated()
    return {"applied": applied, "legacy_remaining": len(leftover)}
