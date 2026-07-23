from __future__ import annotations

from typing import Any

from pkg import auth
from service import invoice_service


def handle_list_invoices(headers: dict[str, str]) -> list[dict[str, Any]]:
    auth.authenticate(headers)
    try:
        return invoice_service.list_invoices()
    finally:
        auth.clear_auth()


def handle_get_invoice(headers: dict[str, str], invoice_id: str) -> dict[str, Any]:
    auth.authenticate(headers)
    try:
        return invoice_service.get_invoice(invoice_id)
    finally:
        auth.clear_auth()


def handle_admin_export(headers: dict[str, str]) -> list[dict[str, Any]]:
    auth.authenticate(headers)
    try:
        return invoice_service.admin_export_invoices()
    finally:
        auth.clear_auth()
