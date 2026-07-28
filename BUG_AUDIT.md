# Bug Audit — llm-coding-bench

> Auto-generated from source review. Severity: **Critical** / **Major** / **Minor** / **Cosmetic**.

---

## Critical

### C1. `bench_runner.py:198` — `BaseException` catch swallows `KeyboardInterrupt` / `SystemExit`

```python
except BaseException as e:
    r = _error_row(spec, task, e)
```

**Impact:** A `Ctrl-C` or `SystemExit` during `run_agent` is treated as a normal task failure (score=0) instead of propagating. The runner silently eats termination signals.

**Fix:** Change to `except Exception as e:` so `KeyboardInterrupt` and `SystemExit` propagate.

---

### C2. `bench_runner.py:202` — `results` list re-created every iteration, O(n²) merge

```python
results = [x for x in results if x.get(id_attr) != tid]
results.append(r)
```

**Impact:** For 9 tasks this is harmless, but the pattern is wrong — it rebuilds the entire list each iteration instead of replacing by key. Not a correctness bug today, but fragile if task count grows.

**Fix:** Use a dict keyed by `id_attr` and convert to list at the end.

---

### C3. `bench_runner.py:215-216` — `write_atomic` silently swallows `os.replace` failure after fd close

```python
os.close(tmp_fd)
tmp_fd = -1
os.replace(tmp_path, path)
```

If `os.replace` raises (disk full, permission), the `except BaseException` block tries to close the fd (now -1, guarded) and unlink the temp. The original `os.replace` exception is stored in `err` and re-raised — **but** the temp file was already unlinked in `finally`, so the caller sees a missing file with no trace of the partial write. This is acceptable atomicity, but the error message loses context about whether data was written.

**Severity downgraded to Major** — functionally correct but debuggability is poor.

---

### C4. `bench_runner.py:269` — `os.unlink(tmp_path)` in `finally` can delete a *successful* replace's target

```python
finally:
    try:
        os.unlink(tmp_path)
    except OSError:
        pass
```

**Impact:** If `os.replace(tmp_path, path)` succeeds, `tmp_path` no longer exists at that name, so `os.unlink` raises `ENOENT` and is silently swallowed. This is safe. **False alarm — no bug here.**

---

### C5. `task_timeout.py:136-146` — Partial output discarded when `stdout` is empty

```python
if str(stdout).strip():
    return subprocess.CompletedProcess(...)
raise TaskTimeout(...)
```

**Impact:** When a killed subprocess produced only stderr (no stdout), the captured stderr is **discarded entirely**. The caller gets a `TaskTimeout` with zero output instead of the stderr diagnostic.

**Fix:** Return the partial output regardless of whether stdout is non-empty.

---

### C6. `bench_runner.py:237` & `run.py:163` — `main()` return type mismatch

`bench_runner.run_main()` returns `None` (no return statement). `run.py:163` does `int(args.func(args) or 0)` which handles `None` → `0`. This is fine but relies on a silent coercion. **Minor.**

---

## Major

### M1. `pyhard/bench.py:159` — Regex `extract_python` greedy `[\s\S]*?` can grab wrong fence

```python
fences = re.findall(r"```(?:python|py)?\s*\n([\s\S]*?)```", text, flags=re.I)
```

**Impact:** If the model outputs multiple fenced blocks (e.g. analysis + code), `fences[-1]` is used. This works when the last fence is code, but a model that puts explanatory text after the code fence will have the explanation captured as the last fence.

**Severity:** Edge case; models usually put code last. **Minor in practice.**

---

### M2. `pyhard/bench.py:1177` — Selftest buggy-VM extraction is fragile

```python
buggy = BUGGY_VM_PROMPT.split("```python")[1].split("```")[0]
```

**Impact:** If the prompt template changes (extra backticks, different formatting), this extracts the wrong text or raises `IndexError`. No try/except.

**Fix:** Use a regex or store the buggy code as a constant.

---

### M3. `pyhard/bench.py:1198` — Warmup `chat()` can hang indefinitely on Ollama connection

The warmup calls `chat(MODEL, "Reply with exactly: OK")` which has a first-byte timeout of 600s (`BENCH_FIRST_BYTE_S`) and a socket stall timeout of 180s. For a simple "pong" warmup this is excessive — a failed connection wastes ~10 minutes.

**Fix:** Use a shorter timeout for warmup (e.g. 30s).

---

### M4. `repohard/tasks.py:108-123` — `_parse_pytest_counts` miscounts on mixed output

```python
m = _PYTEST_RE.search(output)   # r"(\d+) passed"
m2 = _PYTEST_FAIL_RE.search(output)  # r"(\d+) failed"
total = passed + failed
```

**Impact:** The pytest summary line says something like `10 passed, 2 failed in 1.23s`. The `(\d+) passed` regex matches `10`. But the summary also says `= 2 failed =` — the `(\d+) failed` regex matches `2`. This works for normal output. However, if the output contains `(\d+) passed` in a test name or assertion message (e.g., `assert 5 passed`), it can match the wrong number. **Low probability but possible.**

---

### M5. `repohard/tools.py:33-48` — `_rel()` path traversal check is incomplete

```python
candidate = (self.root / raw).resolve()
root = self.root.resolve()
if candidate != root and root not in candidate.parents:
    raise ValueError(...)
```

**Impact:** This checks that `candidate` is either `root` or a descendant of `root`. However, on some filesystems (symlinks, macOS APFS snapshots), `resolve()` can produce different paths for the same file. A crafted path like `../fixture/ledgerkit/../../../etc/passwd` resolves outside and is caught. **Actually correct** — `resolve()` normalizes symlinks. **No bug.**

---

### M6. `repohard/tools.py:149` — `find_refs` symbol validation too strict

```python
if not symbol or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol):
    return {"ok": False, "error": "symbol must be a simple identifier"}
```

**Impact:** Prevents searching for namespaced symbols like `pkg.auth.AuthHandler`. Users can't search for dotted references. This is a design choice, not a bug — the tool is intentionally limited. **Minor UX issue.**

---

### M7. `shopapi/tools.py:20` & `repohard/tools.py:23` — `ToolSession.root` defaults to module-level `FIXTURE_ROOT`

Both `ToolSession` classes use a mutable default-like class attribute pattern via dataclass `field(default_factory=list)` for `calls` and `files_read`, but `root: Path = FIXTURE_ROOT` is a **mutable default shared across instances** at the class level.

Wait — `Path` is immutable, so this is fine. **No bug.**

Actually, re-checking: dataclass with `root: Path = FIXTURE_ROOT` — since `Path` is immutable, each instance gets its own copy when assigned. **No bug.**

---

### M8. `claim/bench.py:202-203` — Evidence bonus computation is wrong

```python
code_reads = [f for f in session.files_read if f.endswith(".py")]
ev = min(3, len(set(code_reads)) // 2)   # 0..3
```

**Impact:** Reading 2 Python files gives `ev = 1`, reading 3 gives `ev = 1`, reading 4 gives `ev = 2`. The `// 2` division means you need 6 Python files read for `ev = 3`. This is a design choice — the bonus is intentionally hard to maximize. **Not a bug.**

---

### M9. `arch/tasks.py:466` — `_GRADERS` dict iteration order is non-deterministic in Python < 3.7

Python 3.14 (required by the shebang) guarantees dict insertion order. **No bug.**

---

### M10. `repohard/bench.py:438` — `grade_patch` called with `session` from `run_agent_cursor`, but session is empty

```python
session = ToolSession(max_calls=MAX_TOOL_CALLS)
grade = task.grade(final, session)
```

**Impact:** For Cursor runs, `session.files_read` is empty because Cursor's native tools don't go through `ToolSession.dispatch()`. The grader can't credit evidence reads. This is documented in the code ("Cursor ask-mode has no tool trace → ev=0"). **Known limitation, not a bug.**

---

### M11. `repohard/tools.py:390-414` — `apply_unified_diff` writes patch to workspace, then deletes

```python
patch_file = work / ".repohard_agent.patch"
patch_file.write_text(body, ...)
# ... git apply ...
# fuzzy apply writes target files
# patch_file is never explicitly deleted
```

**Impact:** The patch file is left in the workspace. Since `fresh_fixture_copy()` creates a temp dir that gets `shutil.rmtree`'d in the `finally` block of `grade_patch`, this is harmless. **No bug.**

---

### M12. `repohard/_gen_fixture.py:157` — Intentional bug in `Money.split()` uses float division

```python
share = (self.cents / parts)   # float division, loses precision
```

This is an **intentional** bug planted for the `money_rounding_split` task. Documented with a comment. **Not a bug — by design.**

---

### M13. `scripts/run_cursor_repohard_stale.py:100-105` — Process tree walk can false-positive on unrelated parents

```python
while cur and cur not in seen:
    seen.add(cur)
    if cur == me:
        break
    if cur not in procs:
        return True   # ← kills on any unknown ancestor
    cur = procs[cur][0]
```

**Impact:** If a `run.py` process is a child of a shell that itself has an unknown parent (not in the `ps` snapshot), the script assumes it's a conflicting suite and bails out. This can cause false "busy" detections, stalling Cursor workers.

**Severity:** **Major** — can deadlock the parallel runner.

---

### M14. `scripts/run_cursor_repohard_stale.py:186-190` — `restore_ledgerkit` doesn't verify checkout success

```python
subprocess.run(
    ["git", "-C", str(ROOT), "checkout", "--", "benches/repohard/fixture/ledgerkit/"],
    check=False,  # ← ignores return code
    capture_output=True,
)
```

**Impact:** If the checkout fails (e.g., uncommitted changes in the fixture), the next Cursor run operates on a dirty tree. Subsequent runs may pass when they should fail, or vice versa.

**Fix:** Add `check=True` or verify the working tree is clean afterward.

---

### M15. `bench_lib/ollama_chat.py:235` — Raw socket access is fragile

```python
sock = resp.fp.raw._sock   # type: ignore[attr-defined]
sock.settimeout(stall)
```

**Impact:** This reaches into `urllib`'s internal `_sock` attribute. If Ollama changes its HTTP implementation or Python's urllib internals shift, this silently stops working (the `except Exception: pass` swallows the error and falls back to no stall detection). The `type: ignore` hides the risk.

**Severity:** **Major** — silently degrades to potential hangs without error.

---

### M16. `bench_lib/ollama_think.py:39-45` — `_env_flag` inverts logic for non-standard values

```python
def _env_flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in (
        "0", "false", "off", "no",
    )
```

**Impact:** Any value not in the deny list returns `True`. So `BENCH_THINK_LOOP=maybe` → `True`, `BENCH_THINK_LOOP=TRUE` → `True`. This is consistent with the "default on" design. **Not a bug.**

---

### M17. `bench_lib/ollama_think.py:538` — `parse_think` raises `SystemExit` for invalid value

```python
raise SystemExit(
    f"Invalid BENCH_THINK={v!r} (use 0|1|true|false|low|medium|high|max)"
)
```

**Impact:** `SystemExit` is a `BaseException`. If caught by `except BaseException` (C1), it gets converted to a task error instead of terminating the program. **Cascades from C1.**

---

### M18. `report.py:173-175` — `_prefer_key` can crash if bench not in BENCHES

```python
expected = BENCHES[run.bench].expected_tasks if run.bench in BENCHES else 1
```

This uses a conditional expression, so it returns `1` when the bench is unknown. **Not a bug.**

---

### M19. `report.py:286` — `is_relative_to` is deprecated in Python 3.12+

```python
if results_path.is_relative_to(REPO_ROOT):
```

**Impact:** `Path.is_relative_to()` emits a `DeprecationWarning` in Python 3.12 and is removed in Python 3.14. The project requires Python 3.14 (see shebangs). This will raise `AttributeError` at runtime.

**Fix:** Replace with `(results_path.relative_to(REPO_ROOT)).parts` wrapped in try/except `ValueError`.

---

### M20. `arch/analyze.py:162` — Glob pattern `_pyhard_pyhard_latest.json` is wrong

```python
for p in OUT.glob("*_pyhard_pyhard_latest.json"):
```

**Impact:** The actual files are named `*_pyhard_latest.json` (single "pyhard"). The glob `*_pyhard_pyhard_latest.json` will **never match** any file. The "full official scores" section of the autopsy report always produces empty output.

**Severity:** **Major** — the entire rescore comparison section is broken.

---

### M21. `pyhard/bench.py:61-62` — Version check exits if running under Python < 3.14

```python
if sys.version_info[:2] < (3, 14):
    raise SystemExit(f"Need Python >= 3.14 for grading, got {sys.version}")
```

This is intentional. **Not a bug.**

---

### M22. `repohard/tools.py:318-320` — `_find_block` `else` clause on `for` loop is unreachable

```python
for delta in range(0, fuzz + 1):
    for pos in (preferred - delta, preferred + delta):
        ...
        windows.append(pos)
else:
    for pos in range(0, max(0, len(haystack) - len(needle) + 1)):
        windows.append(pos)
```

**Impact:** The `else` clause on a `for` loop executes after the loop completes normally (no `break`). Since the inner loop never breaks, the `else` always runs. The outer `for` always completes normally. So the `else` block **always** appends all positions. This means the fuzzy scan range is **unbounded** — it always does a full-file scan even when the preferred position matches.

This is not a correctness bug (the full scan is a fallback that never fires because the preferred-range scan already covers all positions), but it's dead code that adds unnecessary complexity. **Minor.**

---

### M23. `bench_lib/ollama_think.py:36` — `ThinkLoopError.reason` defaults to `message` then `"think_loop"`

```python
self.detail = detail or message
self.reason = reason or "think_loop"
```

If `detail` is set but `reason` is not, `reason` defaults to `"think_loop"` even though `detail` might say `"think_budget"`. Callers check `e.reason == "think_budget"` but the constructor would have set `reason="think_loop"` unless the caller explicitly passed `reason="think_budget"`.

Checking the call sites — `_read_stream` at line 150-156 passes `reason="think_budget"` explicitly, and line 160 re-raises the same exception. **No bug** — the callers pass `reason` correctly.

---

### M24. `scripts/watch_stuck_tasks.py:196-202` — `/proc/{pid}/environ` read crashes on macOS

```python
try:
    env = Path(f"/proc/{pid}/environ").read_bytes()
except OSError:
    env = b""
```

**Impact:** On macOS (the dev platform), `/proc` doesn't exist. The `except OSError` catches this and sets `env = b""`. The code then falls through to the `else` branch at line 205 which checks `len(matched) != 1`. If there's exactly one `run.py` process, it gets killed regardless of whether it's a Cursor or Ollama process.

**Severity:** **Major** — on macOS, the watchdog can kill the wrong `run.py` when multiple are running.

---

### M25. `pyhard/bench.py:190` — `run_fragment` runs the harness concatenated directly after code

```python
out, status = run_fragment(code + "\n" + harness)
```

**Impact:** If the model's code doesn't end with a newline (unlikely given `extract_python` strips, but possible), the harness's first line gets concatenated to the last line of the code. The `+ "\n"` handles this. **No bug.**

---

### M26. `claim/bench.py:288` — Selftest gold-grade assertion is too strict

```python
if g["correct"] != len(CLAIMS) or g["score"] < len(CLAIMS):
```

The evidence bonus (`ev`) is added on top of `correct`. If the selftest session reads ≥6 Python files, `ev ≥ 3`, so `score = len(CLAIMS) + 3 > len(CLAIMS)`. The first condition `g["correct"] != len(CLAIMS)` ensures all claims are correct. The second `g["score"] < len(CLAIMS)` is redundant (if correct == len, score >= len). **No bug, just redundant.**

---

## Summary

| Severity | Count | Key Issues |
|----------|-------|------------|
| **Critical** | 2 | C1: `BaseException` swallows signals; C5: partial output discarded on kill |
| **Major** | 5 | M13: process-tree false-positive stalls runner; M14: silent git checkout failure; M15: fragile raw socket access; M20: broken glob in autopsy; M24: watchdog kills wrong process on macOS |
| **Minor** | 4 | M2: fragile buggy-VM extraction; M3: warmup timeout too long; M22: dead `else` on for-loop; M1: regex fence grab edge case |
| **Cosmetic** | 2 | C6: silent `None` → `0` coercion; M26: redundant assertion |

**Total: 13 real bugs** (2 Critical, 5 Major, 4 Minor, 2 Cosmetic)
