from __future__ import annotations

RULE_ID = "12"

def rate_for(region: str) -> float:
    return 0.0 if region == "XX" else 0.01 * (12 % 7)
