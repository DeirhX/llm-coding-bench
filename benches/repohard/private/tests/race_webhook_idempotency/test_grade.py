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


def test_duplicate_webhook_sequential_single_payment():
    payload = {
        "webhook_id": "wh-1",
        "tenant_id": "t1",
        "invoice_id": "inv1",
        "account_id": "a1",
        "amount_cents": 500,
    }
    r1 = handle_payment_webhook(payload)
    r2 = handle_payment_webhook(payload)
    # Assignment requires idempotent side effects, not a specific status string.
    assert r1.get("status") == "ok"
    pays = payment_repo.list_by_invoice("inv1")
    assert len(pays) == 1, pays
    ent = entitlement_repo.get("a1")
    assert ent is not None and ent["plan"] == "pro"
    inv = invoice_repo.get("inv1")
    assert inv["status"] == "paid"
    # Replay must not create another payment (status may be "ok" or "duplicate").
    assert r2 is not None
    pays2 = payment_repo.list_by_invoice("inv1")
    assert len(pays2) == 1, pays2


def test_concurrent_same_webhook_single_payment():
    payload = {
        "webhook_id": "wh-conc",
        "tenant_id": "t1",
        "invoice_id": "inv1",
        "account_id": "a1",
        "amount_cents": 500,
    }
    # reset invoice/payments for this case
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
