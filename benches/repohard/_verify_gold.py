#!/usr/bin/env python3.14
"""Verify unpatched fails and gold patches make private tests pass."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "fixture" / "ledgerkit"
PRIVATE = ROOT / "private"
TASKS = [
    "race_webhook_idempotency",
    "tenant_cache_key_collision",
    "money_rounding_split",
    "migration_backfill_hole",
    "nplus1_reconciliation",
    "confused_deputy_admin",
    "client_contract_drift",
    "outbox_poison_retry",
]


def run_pytest(work: Path, task_id: str) -> int:
    env = {
        **dict(**{k: v for k, v in __import__("os").environ.items()}),
        "PYTHONPATH": str(work),
        "PYTHONUTF8": "1",
    }
    test_dir = PRIVATE / "tests" / task_id
    r = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_dir), "-q", "--tb=line"],
        cwd=str(work),
        env=env,
        capture_output=True,
        text=True,
    )
    return r.returncode, r.stdout + r.stderr


def apply_patch(work: Path, patch: Path) -> None:
    # prefer git apply
    r = subprocess.run(
        ["git", "apply", "--unsafe-paths", str(patch)],
        cwd=str(work),
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        # fallback: patch -p1
        r2 = subprocess.run(
            ["git", "apply", "-p1", str(patch)],
            cwd=str(work),
            capture_output=True,
            text=True,
        )
        if r2.returncode != 0:
            raise RuntimeError(f"apply failed:\n{r.stderr}\n{r2.stderr}\n{patch.read_text()[:500]}")


def main() -> int:
    fails = 0
    for tid in TASKS:
        with tempfile.TemporaryDirectory(prefix="rh_un_") as td:
            work = Path(td) / "ledgerkit"
            shutil.copytree(FIXTURE, work)
            code, out = run_pytest(work, tid)
            if code == 0:
                print(f"FAIL expected: unpatched {tid} should fail\n{out}")
                fails += 1
            else:
                print(f"ok unpatched fails: {tid}")

        with tempfile.TemporaryDirectory(prefix="rh_gold_") as td:
            work = Path(td) / "ledgerkit"
            shutil.copytree(FIXTURE, work)
            apply_patch(work, PRIVATE / "gold" / f"{tid}.patch")
            code, out = run_pytest(work, tid)
            if code != 0:
                print(f"FAIL gold {tid}:\n{out}")
                fails += 1
            else:
                print(f"ok gold passes: {tid}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
