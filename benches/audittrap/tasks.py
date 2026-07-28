"""Audittrap task registry: claim battery + fix / wontfix graders."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from bench_lib.assignment import load_markdown_assignment, load_simple_claims_yaml
from benches.audittrap.tools import (
    PRIVATE_ROOT,
    ToolSession,
    apply_unified_diff,
    extract_patch,
    fresh_fixture_copy,
)

_ROOT = Path(__file__).resolve().parent
_ASSIGN = _ROOT / "assignment"
_PREAMBLE = (_ROOT / "preamble.md").read_text(encoding="utf-8")
_CLAIMS_PATH = _ROOT / "claims.yaml"

_CLAIMS_RAW = load_simple_claims_yaml(_CLAIMS_PATH)
CLAIMS: list[tuple[str, str, bool]] = [
    (str(c["id"]), str(c["text"]), bool(c["gold"])) for c in _CLAIMS_RAW
]
if len(CLAIMS) < 16:
    raise SystemExit(f"claims.yaml looks empty/broken: {len(CLAIMS)} from {_CLAIMS_PATH}")

FIX_TASK_IDS = [
    "runner_interrupt_scored",
    "chat_timeout_dropped",
    "subprocess_stderr_dropped",
    "warmup_no_deadline",
]
WONTFIX_TASK_IDS = [
    "sat_assign_cleared",
    "sql_where_inside_join",
]
TASK_IDS = ["claim_battery", *FIX_TASK_IDS, *WONTFIX_TASK_IDS]

_CURSOR_PREAMBLE = """You are working in a small Python package called **miniharness**
(workspace root). Use your built-in tools to inspect the code.

Claims task — finish with:
<arch_final>
{"answers": {"a01": true, "a02": false}, "citations": {"a01": ["path:symbol"]}}
</arch_final>

Repair tickets — finish with either:
<arch_final>
{"status": "patched", "patch": "unified diff (--- a/path +++ b/path)", "reason": "...", "citations": ["path:symbol"]}
</arch_final>
or
<arch_final>
{"status": "unchanged", "reason": "...", "citations": ["path:symbol"]}
</arch_final>
"""


def prompt_for_provider(prompt: str, provider: str) -> str:
    if provider in ("cursor", "cursor-cli", "agent"):
        body = prompt
        idx = body.find("# ")
        if idx > 0:
            body = body[idx:]
        return _CURSOR_PREAMBLE + "\n\n" + body
    return prompt


@dataclass
class Task:
    id: str
    title: str
    prompt: str
    family: str = "fix"
    max_score: int = 10
    grade: Callable[[dict[str, Any], ToolSession], dict[str, Any]] | None = None


def _title_from_md(body: str, fallback: str) -> str:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _parse_pytest_counts(output: str, returncode: int) -> tuple[int, int]:
    passed = 0
    failed = 0
    m = re.search(r"(\d+) passed", output)
    if m:
        passed = int(m.group(1))
    m2 = re.search(r"(\d+) failed", output)
    if m2:
        failed = int(m2.group(1))
    total = passed + failed
    if total == 0 and returncode != 0:
        return 0, 1
    return passed, total


def run_private_pytest(work: Path, task_id: str) -> dict[str, Any]:
    test_dir = PRIVATE_ROOT / "tests" / task_id
    env = os.environ.copy()
    # Package dir is `work` (named miniharness); parent must be on PYTHONPATH.
    env["PYTHONPATH"] = str(work.parent)
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


def coerce_bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes", "t", "1"):
            return True
        if s in ("false", "no", "f", "0"):
            return False
    return None


_WONTFIX_REASON_NEEDLES: dict[str, tuple[str, ...]] = {
    "sat_assign_cleared": ("snap", "restore", "ancestor", "update(snap)", "backtrack"),
    "sql_where_inside_join": ("sibling", "optional", "group", "join", "where", "nest", "regex"),
}

_UNCHANGED_STATUSES = frozenset(
    {"unchanged", "no_change", "wontfix", "wont_fix", "nochange"}
)
_PROTECTED = {
    "sat_assign_cleared": "solver/sat.py",
    "sql_where_inside_join": "solver/sql.py",
}


def _status_of(answer: dict[str, Any] | None) -> str:
    if not answer:
        return ""
    for key in ("status", "action"):
        val = str(answer.get(key) or "").strip().lower()
        if val:
            return val
    return ""


def grade_claim_battery(answer: dict[str, Any] | None, session: ToolSession) -> dict[str, Any]:
    answers = (answer or {}).get("answers") or {}
    if isinstance(answers, list):
        tmp: dict[str, Any] = {}
        if answers and isinstance(answers[0], dict):
            for item in answers:
                tmp[str(item.get("id"))] = item.get("value", item.get("true"))
        else:
            for (cid, _, _), val in zip(CLAIMS, answers):
                tmp[cid] = val
        answers = tmp

    correct = 0
    wrong = 0
    missing = 0
    per: list[dict[str, Any]] = []
    for cid, text, gold in CLAIMS:
        got = coerce_bool(answers.get(cid))
        if got is None:
            missing += 1
            per.append({"id": cid, "gold": gold, "got": None, "ok": False})
            continue
        ok = got is gold
        if ok:
            correct += 1
        else:
            wrong += 1
        per.append({"id": cid, "gold": gold, "got": got, "ok": ok, "claim": text})

    code_reads = [f for f in session.files_read if f.endswith(".py")]
    ev = min(3, len(set(code_reads)) // 2)
    score = correct + ev
    max_score = len(CLAIMS) + 3
    return {
        "ok": correct >= int(0.8 * len(CLAIMS)),
        "score": score,
        "max_score": max_score,
        "correct": correct,
        "wrong": wrong,
        "missing": missing,
        "evidence_bonus": ev,
        "files_read": sorted(session.files_read),
        "per_claim": per,
        "detail": f"{correct}/{len(CLAIMS)} correct, wrong={wrong}, missing={missing}, ev=+{ev}",
        "passed": correct,
        "total": len(CLAIMS),
    }


def grade_patch(answer: dict[str, Any], session: ToolSession, task_id: str) -> dict[str, Any]:
    status = _status_of(answer)
    if status in _UNCHANGED_STATUSES and not extract_patch(answer).strip():
        return {
            "ok": False,
            "score": 0,
            "max_score": 10,
            "detail": f"status={status!r} with no patch",
            "passed": 0,
            "total": 1,
            "files_read": len(session.files_read),
            "tool_calls": len(session.calls),
        }
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
        try:
            import shutil

            shutil.rmtree(work.parent, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass


def grade_wontfix(answer: dict[str, Any], session: ToolSession, task_id: str) -> dict[str, Any]:
    status = _status_of(answer)
    patch = extract_patch(answer)
    reason = str((answer or {}).get("reason") or "")
    reason_l = reason.lower()
    needles = _WONTFIX_REASON_NEEDLES.get(task_id, ())
    reason_ok = any(n in reason_l for n in needles) if needles else bool(reason.strip())

    # Mutating the protected file → automatic zero.
    if patch.strip() and ("---" in patch or "+++" in patch):
        work = fresh_fixture_copy()
        try:
            applied = apply_unified_diff(work, patch)
            protected = _PROTECTED[task_id]
            from benches.audittrap.tools import FIXTURE_ROOT

            orig = (FIXTURE_ROOT / protected).read_text(encoding="utf-8")
            cur = (work / protected).read_text(encoding="utf-8")
            if orig != cur:
                return {
                    "ok": False,
                    "score": 0,
                    "max_score": 10,
                    "detail": f"patched {protected} (expected unchanged)",
                    "passed": 0,
                    "total": 1,
                    "patch_bytes": len(patch.encode("utf-8")),
                    "apply_detail": applied.get("detail"),
                    "files_read": len(session.files_read),
                    "tool_calls": len(session.calls),
                }
        finally:
            import shutil

            shutil.rmtree(work.parent, ignore_errors=True)

    if status not in _UNCHANGED_STATUSES:
        return {
            "ok": False,
            "score": 0,
            "max_score": 10,
            "detail": f"expected status=unchanged, got {status!r}",
            "passed": 0,
            "total": 1,
            "files_read": len(session.files_read),
            "tool_calls": len(session.calls),
        }

    work = fresh_fixture_copy()
    try:
        result = run_private_pytest(work, task_id)
        if not result["ok"]:
            return {
                "ok": False,
                "score": 0,
                "max_score": 10,
                "detail": f"pristine pytest failed: {result.get('output', '')[-500:]}",
                "passed": result.get("passed"),
                "total": result.get("total"),
                "pytest_output": result.get("output"),
                "files_read": len(session.files_read),
                "tool_calls": len(session.calls),
            }
        score = 10 if reason_ok else 7
        return {
            "ok": score == 10,
            "score": score,
            "max_score": 10,
            "detail": f"unchanged ok; reason_ok={reason_ok}",
            "passed": 1 if reason_ok else 0,
            "total": 1,
            "reason": reason[:500],
            "files_read": len(session.files_read),
            "tool_calls": len(session.calls),
        }
    finally:
        import shutil

        shutil.rmtree(work.parent, ignore_errors=True)


def _claim_prompt() -> str:
    block = "\n".join(f'- {cid}: "{text}"' for cid, text, _ in CLAIMS)
    body = f"""# Claims about miniharness

Mark each claim true or false based on the code in this workspace.

Claims:
{block}

Finish with:
<arch_final>
{{
  "answers": {{"a01": true, "a02": false}},
  "citations": {{"a01": ["runner.py:run_tasks"]}}
}}
</arch_final>

Every claim id listed above must appear with a boolean.
"""
    return _PREAMBLE.rstrip() + "\n\n" + body


def build_tasks() -> list[Task]:
    tasks: list[Task] = [
        Task(
            id="claim_battery",
            title="Claim battery",
            family="claim",
            max_score=len(CLAIMS) + 3,
            prompt=_claim_prompt(),
            grade=grade_claim_battery,
        )
    ]
    for tid in FIX_TASK_IDS:
        path = _ASSIGN / f"{tid}.md"
        meta, body = load_markdown_assignment(path)
        title = str(meta.get("title") or _title_from_md(body, tid))
        prompt = _PREAMBLE.rstrip() + "\n\n" + body.strip() + "\n"

        def _make_fix(task_id: str) -> Callable[[dict[str, Any], ToolSession], dict[str, Any]]:
            def _grade(answer: dict[str, Any], session: ToolSession) -> dict[str, Any]:
                return grade_patch(answer, session, task_id)

            return _grade

        tasks.append(
            Task(
                id=tid,
                title=title,
                family="repair",
                max_score=10,
                prompt=prompt,
                grade=_make_fix(tid),
            )
        )
    for tid in WONTFIX_TASK_IDS:
        path = _ASSIGN / f"{tid}.md"
        meta, body = load_markdown_assignment(path)
        title = str(meta.get("title") or _title_from_md(body, tid))
        prompt = _PREAMBLE.rstrip() + "\n\n" + body.strip() + "\n"

        def _make_wont(task_id: str) -> Callable[[dict[str, Any], ToolSession], dict[str, Any]]:
            def _grade(answer: dict[str, Any], session: ToolSession) -> dict[str, Any]:
                return grade_wontfix(answer, session, task_id)

            return _grade

        tasks.append(
            Task(
                id=tid,
                title=title,
                family="repair",
                max_score=10,
                prompt=prompt,
                grade=_make_wont(tid),
            )
        )
    return tasks


def gold_patch(task_id: str) -> str:
    return (PRIVATE_ROOT / "gold" / f"{task_id}.patch").read_text(encoding="utf-8")


def gold_wontfix(task_id: str) -> dict[str, Any]:
    if task_id == "sat_assign_cleared":
        return {
            "status": "unchanged",
            "reason": "snap captures ancestors after assign[v]=bit; clear+update(snap)+del restores correctly",
            "citations": ["solver/sat.py:dpll"],
        }
    if task_id == "sql_where_inside_join":
        return {
            "status": "unchanged",
            "reason": "JOIN and WHERE are sibling optional groups; WHERE-without-JOIN parses",
            "citations": ["solver/sql.py:_SELECT_RE"],
        }
    raise KeyError(task_id)
