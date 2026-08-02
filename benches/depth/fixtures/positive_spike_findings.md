CLAIM: In multi-round agents like Ollama, if a timeout is detected between rounds, the loop breaks but previous output is preserved in the transcript and result row.
EVIDENCE: benches/repohard/bench.py:279-283
QUOTE:
        if time.perf_counter() >= deadline:
            timed_out = True
            totals["done_reason"] = "task_timeout"
            transcript.add_note("TIMEOUT: exceeded BENCH_TASK_TIMEOUT_S")
            break

CLAIM: The result row returned by `run_agent_ollama` includes the transcript path and the last produced content even after a timeout.
EVIDENCE: benches/repohard/bench.py:421-422
QUOTE:
            "transcript": str(transcript_path),
            "raw_content": last_content[:8000],

CLAIM: If a task execution raises an exception that is caught by the shared runner's main loop, any output produced during that call is lost and replaced by an error row.
EVIDENCE: bench_lib/bench_runner.py:207-210
QUOTE:
            try:
                r = spec.run_agent(task)
            except Exception as e:
                r = _error_row(spec, task, e)

CLAIM: The `_error_row` function generates a result row for timeouts that contains no model output or transcript fields.
EVIDENCE: bench_lib/bench_runner.py:117-135
QUOTE:
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

CLAIM: When a subprocess times out in `subprocess_with_hard_timeout`, partial stdout and stderr captured before the timeout are returned if they are not empty.
EVIDENCE: bench_lib/task_timeout.py:136-142
QUOTE:
        if str(stdout).strip() or str(stderr).strip():
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=-9,
                stdout=str(stdout)[-2000:] if str(stdout).strip() else "",
                stderr=f"[KILLED after {wall:.1f}s] {str(stderr)[-2000:]}",
            )