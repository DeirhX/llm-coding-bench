from __future__ import annotations
from billing.fx import pair_01

def to_usd(cents: int, currency: str) -> int:
    if currency == "USD":
        return cents
    return pair_01.convert(cents)
