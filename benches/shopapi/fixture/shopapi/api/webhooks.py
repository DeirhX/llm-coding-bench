from __future__ import annotations

from typing import Any

from pkg import auth
from service import payment_service


def handle_payment_webhook(headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    """POST /webhooks/payment"""
    auth.authenticate(headers)
    try:
        return payment_service.handle_payment_webhook(body)
    finally:
        auth.clear_auth()
