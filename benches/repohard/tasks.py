"""Repohard task registry + private-pytest graders."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from bench_lib.assignment import load_markdown_assignment
from benches.repohard.tools import (
    PRIVATE_ROOT,
    ToolSession,
    apply_unified_diff,
    extract_patch,
    fresh_fixture_copy,
)

_ROOT = Path(__file__).resolve().parent
_ASSIGN = _ROOT / "assignment"
_PREAMBLE = (_ROOT / "preamble.md").read_text(encoding="utf-8")

TASK_IDS = [
    "race_webhook_idempotency",
    "tenant_cache_key_collision",
    "money_rounding_split",
    "migration_backfill_hole",
    "nplus1_reconciliation",
    "confused_deputy_admin",
    "client_contract_drift",
    "outbox_poison_retry",
]


@dataclass
class Task:
    id: str
    title: str
    prompt: str
    family: str = "fix"
    max_score: int = 10
    grade: Callable[[dict[str, Any], ToolSession], dict[str, Any]] | None = None


_CURSOR_PREAMBLE = """You are fixing bugs in a mid-size Python service repo called **ledgerkit**.
Use your built-in tools to explore the workspace (this directory is the ledgerkit root).
You cannot see private grading tests.

When done, reply with ONLY:
<arch_final>
{"patch": "unified diff (--- a/path +++ b/path)"}
</arch_final>
"""


def prompt_for_provider(prompt: str, provider: str) -> str:
    if provider in ("cursor", "cursor-cli", "agent"):
        # strip ollama tool protocol; keep assignment body
        body = prompt
        if "When you have a fix" in body:
            # replace leading preamble
            idx = body.find("# ")
            if idx > 0:
                body = body[idx:]
        return _CURSOR_PREAMBLE + "\n\n" + body
    return prompt


def _title_from_md(body: str, fallback: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def run_private_pytest(work: Path, task_id: str) -> dict[str, Any]:
    test_dir = PRIVATE_ROOT / "tests" / task_id
    env = os.environ.copy()
    env["PYTHONPATH"] = str(work)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_dir), "-q", "--tb=line"],
        cwd=str(work),
        env=env,
        capture_output=True,
        text=True,
    )
    out = (r.stdout or "") + (r.stderr or "")
    passed, total = _parse_pytest_counts(out, r.returncode)
    return {
        "returncode": r.returncode,
        "passed": passed,
        "total": total,
        "output": out[-4000:],
        "ok": r.returncode == 0 and total > 0 and passed == total,
    }


_PYTEST_RE = re.compile(r"(\d+) passed")
_PYTEST_FAIL_RE = re.compile(r"(\d+) failed")
_PYTEST_SUMMARY = re.compile(r"=+\s*(.*?)\s*=+\s*$", re.M)


def _parse_pytest_counts(output: str, returncode: int) -> tuple[int, int]:
    passed = 0
    failed = 0
    m = _PYTEST_RE.search(output)
    if m:
        passed = int(m.group(1))
    m2 = _PYTEST_FAIL_RE.search(output)
    if m2:
        failed = int(m2.group(1))
    total = passed + failed
    if total == 0 and returncode != 0:
        # collection error etc.
        return 0, 1
    if total == 0 and returncode == 0:
        return 0, 0
    return passed, total


def grade_patch(answer: dict[str, Any], session: ToolSession, task_id: str) -> dict[str, Any]:
    patch = extract_patch(answer)
    preview = patch[:1200]
    work = fresh_fixture_copy()
    try:
        applied = apply_unified_diff(work, patch)
        if not applied["ok"]:
            return {
                "ok": False,
                "score": 0,
                "max_score": 10,
                "detail": f"patch_apply: {applied['detail']}",
                "passed": 0,
                "total": 0,
                "patch_bytes": len(patch.encode("utf-8")),
                "patch_preview": preview,
                "apply_detail": applied.get("detail"),
                "files_read": len(session.files_read),
                "tool_calls": len(session.calls),
            }
        result = run_private_pytest(work, task_id)
        total = int(result["total"] or 0)
        passed = int(result["passed"] or 0)
        score = 10 if result["ok"] else int(round(10 * (passed / total))) if total else 0
        return {
            "ok": bool(result["ok"]),
            "score": score,
            "max_score": 10,
            "detail": f"pytest {passed}/{total} ({applied.get('detail')})",
            "passed": passed,
            "total": total,
            "pytest_output": result.get("output"),
            "patch_bytes": len(patch.encode("utf-8")),
            "patch_preview": preview,
            "apply_detail": applied.get("detail"),
            "files_read": len(session.files_read),
            "tool_calls": len(session.calls),
        }
    finally:
        # best-effort cleanup
        try:
            import shutil

            shutil.rmtree(work.parent, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass


def build_tasks() -> list[Task]:
    tasks: list[Task] = []
    for tid in TASK_IDS:
        path = _ASSIGN / f"{tid}.md"
        _meta, body = load_markdown_assignment(path)
        title = _title_from_md(body, tid)
        prompt = _PREAMBLE.rstrip() + "\n\n" + body.strip() + "\n"

        def _make_grade(task_id: str) -> Callable[[dict[str, Any], ToolSession], dict[str, Any]]:
            def _grade(answer: dict[str, Any], session: ToolSession) -> dict[str, Any]:
                return grade_patch(answer, session, task_id)

            return _grade

        tasks.append(
            Task(
                id=tid,
                title=title,
                prompt=prompt,
                grade=_make_grade(tid),
            )
        )
    return tasks


def gold_patch(task_id: str) -> str:
    return (PRIVATE_ROOT / "gold" / f"{task_id}.patch").read_text(encoding="utf-8")
