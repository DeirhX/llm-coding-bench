from __future__ import annotations

from store.migrations import m001_init, m002_backfill_ledger

MIGRATIONS = [
    ("001_init", m001_init.apply),
    ("002_backfill_ledger", m002_backfill_ledger.apply),
]
