from __future__ import annotations

from typing import Any, Callable

from store import outbox


class PoisonError(RuntimeError):
    pass


def default_publish(event: dict[str, Any]) -> None:
    if event.get("payload", {}).get("poison"):
        raise PoisonError("poison pill")
    # pretend publish succeeded
    return None


def process_once(publish: Callable[[dict], None] | None = None) -> dict[str, Any]:
    """Drain outbox batch."""
    publish = publish or default_publish
    batch = outbox.claim_batch()
    ok = 0
    failed = 0
    for row in batch:
        try:
            publish(row)
            outbox.mark_published(row["id"])
            ok += 1
        except Exception:
            failed += 1
        outbox.ack(row["id"])
    return {"ok": ok, "failed": failed, "batch": len(batch)}
