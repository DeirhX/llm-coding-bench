from __future__ import annotations

from service import payment_service


def payment(body=None, headers=None, **_):
    body = body or {}
    return {"status": 200, **payment_service.handle_payment_webhook(body)}
