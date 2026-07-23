from __future__ import annotations
# decoy legacy shim

def old_amount_field(row: dict) -> float:
    return float(row.get("amount", 0)) / 100.0
