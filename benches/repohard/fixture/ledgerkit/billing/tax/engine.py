from __future__ import annotations
from billing.tax import rule_01

def tax_cents(cents: int, region: str) -> int:
    r = rule_01.rate_for(region)
    return int(cents * r)
