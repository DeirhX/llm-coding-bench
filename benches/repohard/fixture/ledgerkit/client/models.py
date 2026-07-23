from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class InvoiceDTO:
    id: str
    tenant_id: str
    account_id: str
    amount: float
    currency: str
    status: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "InvoiceDTO":
        if "amount" in data:
            amount = float(data["amount"])
        elif "amount_cents" in data:
            amount = float(data["amount_cents"])
        else:
            amount = 0.0
        return cls(
            id=data["id"],
            tenant_id=data["tenant_id"],
            account_id=data["account_id"],
            amount=amount,
            currency=data.get("currency", "USD"),
            status=data["status"],
        )
