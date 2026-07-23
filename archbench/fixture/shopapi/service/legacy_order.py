"""Decoy module — NOT wired into api/orders DELETE path.

Models that grep 'cancel' and assume every cancel_* is live will fail claim c11.
"""

from __future__ import annotations

from typing import Any


def cancel_order_legacy(order_id: str) -> dict[str, Any]:
    """Dead code. Do not call. Not referenced by api.orders.handle_delete_order."""
    return {"id": order_id, "status": "legacy_cancelled"}
