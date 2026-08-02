"""Shared benchmark main loop.

Extracts the ~120-line main() body duplicated across pyhard, arch, claim and
repohard: selftest guard, task selection, merge-latest, warmup, per-task
dispatch loop, JSON-write, latest-write, and summary.

Each bench provides a ``BenchSpec`` dataclass with the few caller-specific parts
(task loader, agent runner, warmup callback) and calls ``run_main(spec)``.

Usage::

    from bench_lib.bench_runner import BenchSpec, TaskFields, run_main

    def _warmup():
        # bench-specific warmup (ollama chat / cursor chat / fixture copy)
        ...

    spec = BenchSpec(
        bench_name   = "repohard",
        tag_suffix   = "repohard",
        model        = MODEL,
        tag          = TAG,
        provider     = PROVIDER,
        out_dir      = OUT_DIR,
        merge_latest = MERGE_LATEST,
        warmup       = _warmup,
        load_tasks   = select_tasks,    # () -> list[Task]
        run_agent    = run_agent,       # (task,) -> dict
        task_fields  = TaskFields(
            id_attr="id",
            row_id_key="task",  # JSON key; must match what run_agent emits
            exclude_from_print=("tool_trace", "pytest_output"),
        ),
        post_loop_hook = my_post_loop,   # optional
    )
    run_main(spec)

Selftests belong in each bench's ``main()``: point ``out_dir`` at a temp
directory when ``MODEL == "selftest"`` so smoke runs never write under
``results/``. ``run_main`` itself has no selftest mode.

"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Caller-supplied parts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskFields:
    """Attribute names on the task object and keys in the result row."""

    id_attr: str = "id"        # Attribute name on the Task object
    row_id_key: str = "task"   # JSON key; repo convention (must match emit)
    title_attr: str = "title"
    family_attr: str = "family"
    max_score_attr: str = "max_score"

    # Extra keys to exclude from per-task stdout print.
    exclude_from_print: tuple[str, ...] = (
        "tool_trace", "answer", "per_claim", "pytest_output", "raw_content"
    )


@dataclass(frozen=True)
class BenchSpec:
    """One-time configuration provided by the bench file."""

    bench_name: str
    tag_suffix: str                # suffix appended to JSON log filename
    model: str
    tag: str
    provider: str
    out_dir: Path
    merge_latest: bool
    warmup: Callable[[], None]     # bench-specific warmup
    load_tasks: Callable[[], list[Any]]
    run_agent: Callable[[Any], dict[str, Any]]
    task_fields: TaskFields = field(default_factory=TaskFields)
    latest_suffix: str = "_latest.json"
    post_loop_hook: (
        Callable[[list[dict[str, Any]], str], None] | None
    ) = None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _safe_get(task: Any, attr: str, default: Any = None) -> Any:
    try:
        return getattr(task, attr)
    except AttributeError:
        return default


def _error_row(spec: BenchSpec, task: Any, e: BaseException) -> dict[str, Any]:
    """Construct an error result row."""
    is_timeout = (
        isinstance(e, (TimeoutError, subprocess.TimeoutExpired))
        or "timed out" in str(e).lower()
    )
    tid = _safe_get(task, spec.task_fields.id_attr, "unknown")
    return {
        "model": spec.model,
        "provider": spec.provider,
        spec.task_fields.row_id_key: tid,
        "title": _safe_get(task, spec.task_fields.title_attr, tid),
        "family": _safe_get(task, spec.task_fields.family_attr, ""),
        "ok": False,
        "score": 0,
        "max_score": _safe_get(task, spec.task_fields.max_score_attr, 0),
        "grade_detail": (
            f"ERROR: {type(e).__name__}: {e}"
            if not is_timeout
            else (
                f"TIMEOUT: exceeded BENCH_TASK_TIMEOUT_S / "
                f"Cursor timeout ({e})"
            )
        ),
        "done_reason": "task_timeout" if is_timeout else "error",
    }


# ---------------------------------------------------------------------------
# Shared main loop
# ---------------------------------------------------------------------------


def run_main(spec: BenchSpec) -> None:
    """Run the shared benchmark main loop.

    Flow:
        1. Merge most-recent ``*_latest.json`` (pre-fill completed tasks).
        2. Warmup (model availability check).
        3. Iterate tasks -> run_agent -> write merged JSON + latest + log.
        4. Post-loop hook (optional).
        5. Print & write summary.
    """
    stamp = time.strftime("%Y%m%d_%H%M%S")

    # -- paths -------------------------------------------------------------
    # Tags already end with the bench name for most benches; do not double it.
    suffix = spec.tag_suffix or spec.bench_name
    stem = spec.tag
    if suffix and not (stem == suffix or stem.endswith(f"_{suffix}")):
        stem = f"{stem}_{suffix}"
    out_json = spec.out_dir / f"{stem}_{stamp}.json"
    out_log = spec.out_dir / f"{spec.tag}.log"
    latest_path = spec.out_dir / f"{spec.tag}_latest.json"
    summary_path = spec.out_dir / f"{spec.tag}_summary.json"

    # -- merge latest (pre) -----------------------------------------------
    results_map: dict[str, dict[str, Any]] = {}
    row_id_key = spec.task_fields.row_id_key
    if spec.merge_latest and latest_path.is_file():
        try:
            prev = json.loads(
                latest_path.read_text(encoding="utf-8")
            )
            if isinstance(prev, list):
                for r in prev:
                    if isinstance(r, dict) and (tid := r.get(row_id_key)) is not None:
                        results_map[str(tid)] = r
        except (OSError, json.JSONDecodeError) as e:
            print(f"Warning: failed to merge latest results from {latest_path}: {e}", file=sys.stderr)

    # -- warmup -----------------------------------------------------------
    try:
        spec.warmup()
    except Exception as e:
        print(f"Warmup failed: {e}", file=sys.stderr)
        sys.exit(1)

    # -- per-task loop --------------------------------------------------
    done_ids = set(results_map.keys())

    with out_log.open("a", encoding="utf-8") as log:
        log.write(
            f"\n==== {spec.bench_name} provider={spec.provider} "
            f"{spec.model} tag={spec.tag} {stamp} ====\n"
        )

        for i, task in enumerate(spec.load_tasks()):
            tid = _safe_get(task, spec.task_fields.id_attr)
            str_tid = str(tid) if tid is not None else f"unknown_{i}"
            if spec.merge_latest and str_tid in done_ids:
                print(f"-- {tid} ... skip (merged)", flush=True)
                continue

            print(f"-- {tid} ...", flush=True)
            log.write(f"-- {tid} ...\n")

            try:
                r = spec.run_agent(task)
            except Exception as e:
                r = _error_row(spec, task, e)

            # -- merge into results -------------------------------------------
            results_map[str_tid] = r

            # Log and print the individual result
            r_json = json.dumps(r, indent=2)
            exclude = spec.task_fields.exclude_from_print
            print(
                json.dumps({k: r[k] for k in r if k not in exclude}, indent=2),
                flush=True,
            )
            log.write(r_json + "\n")

            # Write every time to maximize safety; task counts are small enough that O(N^2) is negligible.
            merged = json.dumps(list(results_map.values()), indent=2)
            write_atomic(merged, out_json)
            write_atomic(merged, latest_path)

    # -- post-loop hook (optional) -----------------------------------------
    results = list(results_map.values())
    if spec.post_loop_hook:
        spec.post_loop_hook(results, stamp)

    # -- summary -------------------------------------------------------------
    total = sum(r.get("score", 0) for r in results)
    mx = sum(r.get("max_score", 0) for r in results)
    passed = sum(1 for r in results if r.get("ok"))

    summary = {
        "model": spec.model,
        "tag": spec.tag,
        "score": total,
        "max_score": mx,
        "pass": passed,
        "tasks": len(results),
        "path": str(out_json),
    }
    print("SUMMARY", json.dumps(summary))
    write_atomic(json.dumps(summary, indent=2), summary_path)
    return None


def write_atomic(contents: str, path: Path) -> None:
    """Write ``contents`` to ``path`` atomically via tmp file + fsync.

    Handles fd cleanup correctly to avoid double-close errors that would
    mask the original exception.
    """
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, suffix=".tmp", prefix=f".{path.name}_"
    )
    err: BaseException | None = None
    try:
        os.write(tmp_fd, contents.encode("utf-8"))
        os.fsync(tmp_fd)
        os.close(tmp_fd)
        tmp_fd = -1     # mark as closed so the finally block skips it
        os.replace(tmp_path, path)
    except BaseException as exc:
        err = exc
        # Close fd only if it is still open -- double-close raises OSError
        # which would replace the original exception in the traceback.
        if tmp_fd >= 0:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
    finally:
        # Always try to clean up the temp file -- suppress cleanup errors
        # so the original exception is not replaced.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    if err is not None:
        raise err
