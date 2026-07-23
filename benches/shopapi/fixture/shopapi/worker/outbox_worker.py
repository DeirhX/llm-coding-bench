from __future__ import annotations

from typing import Any, Callable

from store import outbox

Publisher = Callable[[dict[str, Any]], None]


def process_once(publish: Publisher) -> int:
    """Drain a batch of outbox rows."""
    batch = outbox.claim_batch()
    n = 0
    for row in batch:
        outbox.ack(row["id"])
        publish(row)
        outbox.mark_published(row["id"])
        n += 1
    return n
