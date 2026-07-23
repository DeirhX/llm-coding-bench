from __future__ import annotations

from typing import Any, Callable

from store import outbox

Publisher = Callable[[dict[str, Any]], None]


def process_once(publish: Publisher) -> int:
    """Drain a batch of outbox rows.

    PLANTED BUG (duplicate delivery / incident):
    We ack the row BEFORE publish succeeds. If publish raises or the process
    dies after ack, the event is dropped. Conversely, a buggy retry path in
    webhook handling can re-insert duplicates; this worker will deliver each.
    """
    batch = outbox.claim_batch()
    n = 0
    for row in batch:
        # Ack first — wrong order
        outbox.ack(row["id"])
        publish(row)
        outbox.mark_published(row["id"])
        n += 1
    return n
