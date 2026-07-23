from __future__ import annotations

RULE_ID = "04"

def rate_for(region: str) -> float:
    return 0.0 if region == "XX" else 0.01 * (4 % 7)
