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
