#!/usr/bin/env python3.14
"""Regenerate private/gold/*.patch via git diff --no-index (LF-safe)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixture" / "ledgerkit"
GOLD = ROOT / "private" / "gold"

# Import fixed contents from _gen_private by redefining here (source of truth for gold files)
from benches.repohard._gen_private import GOLD_SOURCES, build_gold_sources  # noqa: E402


def git_diff(old_file: Path, new_file: Path, rel: str) -> str:
    r = subprocess.run(
        [
            "git",
            "diff",
            "--no-index",
            "--no-ext-diff",
            "-U3",
            str(old_file),
            str(new_file),
        ],
        capture_output=True,
    )
    # exit 1 means differences; 0 means identical
    raw = r.stdout.decode("utf-8", errors="replace")
    if not raw.strip():
        return ""
    lines = raw.splitlines(keepends=True)
    out = []
    for line in lines:
        if line.startswith("diff --git"):
            out.append(f"diff --git a/{rel} b/{rel}\n")
        elif line.startswith("--- "):
            out.append(f"--- a/{rel}\n")
        elif line.startswith("+++ "):
            out.append(f"+++ b/{rel}\n")
        else:
            out.append(line)
    return "".join(out)


def main() -> None:
    # sync race gold with current buggy baseline
    build_gold_sources()
    GOLD_SOURCES["race_webhook_idempotency"] = {
        "service/payment_service.py": (ROOT / "_gold_files" / "payment_service.py").read_text(
            encoding="utf-8"
        )
        if (ROOT / "_gold_files" / "payment_service.py").exists()
        else GOLD_SOURCES["race_webhook_idempotency"]["service/payment_service.py"]
    }

    # Always write canonical gold payment_service
    gold_pay = '''\
from __future__ import annotations

from typing import Any
import uuid
import threading

from store import payment_repo, webhook_repo, outbox, invoice_repo
from service import invoice_service, entitlement_service
from pkg import events
from pkg.tenant import require_tenant, set_tenant, TenantContext


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _LOCKS_GUARD:
        if key not in _LOCKS:
            _LOCKS[key] = threading.Lock()
        return _LOCKS[key]


def handle_payment_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """Process payment.succeeded webhook (idempotent on webhook_id)."""
    webhook_id = payload["webhook_id"]
    tenant_id = payload["tenant_id"]
    invoice_id = payload["invoice_id"]
    amount_cents = int(payload["amount_cents"])

    lock = _lock_for(webhook_id)
    with lock:
        if webhook_repo.is_processed(webhook_id):
            return {"status": "duplicate", "webhook_id": webhook_id}

        set_tenant(TenantContext(tenant_id=tenant_id))
        pay = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "invoice_id": invoice_id,
            "amount_cents": amount_cents,
            "webhook_id": webhook_id,
        }
        payment_repo.upsert(pay)
        invoice_service.mark_paid(invoice_id)
        inv = invoice_repo.get(invoice_id) or {}
        account_id = inv.get("account_id") or payload.get("account_id")
        if account_id:
            entitlement_service.activate_paid_plan(account_id, tenant_id)
        outbox.insert(events.invoice_paid(tenant_id, invoice_id, pay["id"]))
        webhook_repo.mark_processed(webhook_id, {"payment_id": pay["id"]})
        return {"status": "ok", "payment_id": pay["id"], "webhook_id": webhook_id}


def record_payment(invoice_id: str, amount_cents: int) -> dict[str, Any]:
    ctx = require_tenant()
    pay = {
        "id": str(uuid.uuid4()),
        "tenant_id": ctx.tenant_id,
        "invoice_id": invoice_id,
        "amount_cents": amount_cents,
    }
    return payment_repo.upsert(pay)
'''
    GOLD_SOURCES["race_webhook_idempotency"] = {"service/payment_service.py": gold_pay}

    GOLD.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        for tid, files in GOLD_SOURCES.items():
            parts: list[str] = []
            for rel, new_body in files.items():
                old_path = FIXTURE / rel
                new_path = td_path / tid / rel
                new_path.parent.mkdir(parents=True, exist_ok=True)
                text = new_body.replace("\r\n", "\n")
                if not text.endswith("\n"):
                    text += "\n"
                new_path.write_text(text, encoding="utf-8", newline="\n")
                # normalize old to temp as LF for stable diff
                old_norm = td_path / "old" / tid / rel
                old_norm.parent.mkdir(parents=True, exist_ok=True)
                old_text = old_path.read_text(encoding="utf-8").replace("\r\n", "\n")
                if not old_text.endswith("\n"):
                    old_text += "\n"
                old_norm.write_text(old_text, encoding="utf-8", newline="\n")
                part = git_diff(old_norm, new_path, rel)
                if part:
                    parts.append(part)
            patch_path = GOLD / f"{tid}.patch"
            patch_path.write_text("".join(parts), encoding="utf-8", newline="\n")
            print(f"wrote {patch_path.name} ({patch_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
