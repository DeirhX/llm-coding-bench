from __future__ import annotations

from typing import Any

from pkg import auth
from service import order_service


def handle_list_orders(headers: dict[str, str]) -> list[dict[str, Any]]:
    auth.authenticate(headers)
    try:
        return order_service.list_orders()
    finally:
        auth.clear_auth()


def handle_get_order(headers: dict[str, str], order_id: str) -> dict[str, Any]:
    auth.authenticate(headers)
    try:
        return order_service.get_order(order_id)
    finally:
        auth.clear_auth()


def handle_delete_order(headers: dict[str, str], order_id: str) -> dict[str, Any]:
    """DELETE /orders/{id}"""
    auth.authenticate(headers)
    try:
        return order_service.cancel_order(order_id)
    finally:
        auth.clear_auth()
