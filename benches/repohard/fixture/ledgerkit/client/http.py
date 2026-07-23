from __future__ import annotations

from typing import Any, Callable

from client.models import InvoiceDTO


class LedgerClient:
    """Thin typed client used by sibling services."""

    def __init__(self, handle: Callable[..., dict]):
        self._handle = handle

    def get_invoice(self, invoice_id: str, headers: dict[str, str]) -> InvoiceDTO:
        resp = self._handle("GET", f"/v1/invoices/{invoice_id}", headers=headers)
        if resp.get("status") != 200:
            raise RuntimeError(resp)
        return InvoiceDTO.from_api(resp["invoice"])

    def create_invoice(self, body: dict[str, Any], headers: dict[str, str]) -> InvoiceDTO:
        resp = self._handle("POST", "/v1/invoices", headers=headers, body=body)
        if resp.get("status") != 201:
            raise RuntimeError(resp)
        return InvoiceDTO.from_api(resp["invoice"])
