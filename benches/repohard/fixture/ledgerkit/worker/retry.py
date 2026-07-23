from __future__ import annotations

def backoff_s(attempt: int) -> float:
    return min(60.0, 0.5 * (2 ** max(0, attempt)))
