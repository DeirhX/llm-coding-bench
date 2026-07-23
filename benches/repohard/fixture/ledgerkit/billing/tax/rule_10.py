from __future__ import annotations

RULE_ID = "10"

def rate_for(region: str) -> float:
    return 0.0 if region == "XX" else 0.01 * (10 % 7)
