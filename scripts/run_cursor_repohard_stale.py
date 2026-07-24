#!/usr/bin/env python3
"""Re-run Cursor repohard for pre-isolation (no answer.patch) models.

Runs up to BENCH_PARALLEL Cursor suites at once. Starts a model only when
local `run.py run repohard` is idle, then restores ledgerkit under a lock.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "repohard"
LOCK = OUT / "ledgerkit_restore.lock"
PY = os.environ.get("BENCH_PYTHON") or str(ROOT / ".venv" / "bin" / "python")
if not Path(PY).exists():
    PY = sys.executable

PARALLEL = max(1, int(os.environ.get("BENCH_PARALLEL", "2")))
WAIT_MATRIX = os.environ.get("BENCH_WAIT_MATRIX", "0") == "1"

MODELS = [
    "composer-2.5",
    "claude-sonnet-5-high",
    "claude-opus-4-8-thinking-high",
    "claude-4.5-haiku",
    "gpt-5.6-sol-high",
    "gpt-5.6-terra-high",
    "gpt-5.6-luna-high",
    "cursor-grok-4.5-high",
    "gemini-3.6-flash-high",
]


def log(msg: str) -> None:
    print(msg, flush=True)


def now() -> str:
    return datetime.now().strftime("%a %b %d %H:%M:%S %Z %Y")


def safe_name(model: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in model)


def latest_path(model: str) -> Path:
    return OUT / f"cursor_{safe_name(model)}_repohard_latest.json"


def has_patches(model: str) -> bool:
    p = latest_path(model)
    if not p.exists():
        return False
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(rows, list) or len(rows) < 8:
        return False
    n = sum(1 for t in rows if (t.get("answer") or {}).get("patch"))
    return n >= 8


def local_repohard_busy() -> bool:
    """True only when the Ollama matrix tree is running ``run.py run repohard``.

    Cursor workers also exec that argv — matching them deadlocks parallel=2.
    """
    me = os.getpid()
    r = subprocess.run(
        ["ps", "-ax", "-o", "pid=", "-o", "ppid=", "-o", "command="],
        capture_output=True,
        text=True,
    )
    procs: dict[int, tuple[int, str]] = {}
    for line in r.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        procs[pid] = (ppid, parts[2])

    matrix_pids: set[int] = set()
    pidf = ROOT / "results" / "universal_matrix" / "matrix.pid"
    if pidf.exists():
        try:
            matrix_pids.add(int(pidf.read_text().strip()))
        except ValueError:
            pass
    changed = True
    while changed:
        changed = False
        for pid, (ppid, _) in procs.items():
            if ppid in matrix_pids and pid not in matrix_pids:
                matrix_pids.add(pid)
                changed = True

    for pid, (_ppid, cmd) in procs.items():
        if pid == me:
            continue
        if "run.py run repohard" not in cmd:
            continue
        if "run_cursor_repohard_stale" in cmd:
            continue
        if pid in matrix_pids:
            return True
        # No matrix: only treat as busy if this is clearly not our Cursor child
        # pool (parent should be this script). Allow parallel Cursor workers.
        ppid = _ppid
        # Walk parents — if we hit this supervisor, it's a sibling Cursor job.
        seen = set()
        cur = ppid
        while cur and cur not in seen:
            seen.add(cur)
            if cur == me:
                break  # our worker — ignore
            if cur not in procs:
                # Outside our tree and no matrix — could be leftover ollama.
                return True
            cur = procs[cur][0]
        else:
            continue
    return False


def wait_local_repohard_idle(timeout_s: int = 40000) -> None:
    deadline = time.time() + timeout_s
    ticks = 0
    while time.time() < deadline:
        if not local_repohard_busy():
            time.sleep(3)
            if not local_repohard_busy():
                return
        ticks += 1
        if ticks % 20 == 0:
            log(f"waiting for local repohard idle… {now()} (tick={ticks})")
        time.sleep(6)
    log(f"WARN gave up waiting for local repohard idle {now()}")


def wait_matrix_if_requested() -> None:
    if not WAIT_MATRIX:
        return
    matrix_log = ROOT / "results" / "universal_matrix" / "matrix.log"
    log(f"BENCH_WAIT_MATRIX=1 — blocking until matrix ALL DONE {now()}")
    for i in range(4000):
        if matrix_log.exists() and "universal matrix ALL DONE" in matrix_log.read_text(
            encoding="utf-8", errors="replace"
        ):
            log(f"matrix done marker found {now()}")
            return
        if i and i % 18 == 0:
            log(f"still waiting on matrix… {now()} (tick={i})")
        time.sleep(10)
    log(f"WARN matrix wait timed out {now()}")


def restore_ledgerkit() -> None:
    """Exclusive restore so parallel workers don't fight git/copytree."""
    OUT.mkdir(parents=True, exist_ok=True)
    # Atomic create lock file + exclusive open (portable; no flock binary needed).
    while True:
        try:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            break
        except FileExistsError:
            # stale lock?
            try:
                age = time.time() - LOCK.stat().st_mtime
                if age > 120:
                    LOCK.unlink(missing_ok=True)
            except OSError:
                pass
            time.sleep(0.2)
    try:
        os.write(fd, f"{os.getpid()} {now()}\n".encode())
        subprocess.run(
            ["git", "-C", str(ROOT), "checkout", "--", "benches/repohard/fixture/ledgerkit/"],
            check=False,
            capture_output=True,
        )
    finally:
        os.close(fd)
        try:
            LOCK.unlink(missing_ok=True)
        except OSError:
            pass


def force_models() -> set[str]:
    return {
        x.strip()
        for x in os.environ.get("BENCH_FORCE_MODELS", "").split(",")
        if x.strip()
    }


def run_one(model: str) -> int:
    log(f"######## START model={model} {now()} ########")
    if has_patches(model) and model not in force_models():
        log(f"skip already HAS_PATCH {latest_path(model)}")
        return 0

    wait_local_repohard_idle()
    restore_ledgerkit()
    log(f"restored ledgerkit for {model} {now()}")

    latest = latest_path(model)
    if latest.exists():
        bak = latest.with_name(
            latest.name.replace(
                "_latest.json",
                f"_pre_isolation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
            )
        )
        bak.write_bytes(latest.read_bytes())
        log(f"archived {latest} -> {bak}")

    env = os.environ.copy()
    env.update(
        {
            "BENCH_PROVIDER": "cursor",
            "BENCH_OUT": str(ROOT / "results"),
            "BENCH_CURSOR_MODE": env.get("BENCH_CURSOR_MODE", "ask"),
            "BENCH_MERGE_LATEST": "0",
            "BENCH_MODEL": model,
            "BENCH_TAG": f"cursor_{safe_name(model)}_repohard",
            "PATH": f"/usr/bin:/bin:/usr/sbin:/sbin:{Path.home()}/.local/bin:/usr/local/bin:"
            + env.get("PATH", ""),
        }
    )
    for k in ("BENCH_TASKS", "BENCH_THINK", "BENCH_THINK_MAX_CHARS", "BENCH_TAG"):
        # BENCH_TAG set above; clear think knobs
        pass
    env.pop("BENCH_TASKS", None)
    env.pop("BENCH_THINK", None)
    env.pop("BENCH_THINK_MAX_CHARS", None)

    log(f"---- run repohard model={model} tag={env['BENCH_TAG']} ----")
    r = subprocess.run(
        [PY, "-u", str(ROOT / "run.py"), "run", "repohard"],
        cwd=str(ROOT),
        env=env,
    )
    if r.returncode != 0:
        log(f"WARN repohard rc={r.returncode} model={model}")

    if has_patches(model):
        log(f"######## OK HAS_PATCH model={model} {now()} ########")
        return 0

    log(f"######## WARN missing patches model={model} — retry {now()} ########")
    wait_local_repohard_idle()
    restore_ledgerkit()
    env["BENCH_MERGE_LATEST"] = "1"
    r2 = subprocess.run(
        [PY, "-u", str(ROOT / "run.py"), "run", "repohard"],
        cwd=str(ROOT),
        env=env,
    )
    if r2.returncode != 0:
        log(f"WARN retry rc={r2.returncode}")
    if has_patches(model):
        log(f"######## OK after retry model={model} {now()} ########")
        return 0
    log(f"######## FAIL model={model} still no patches {now()} ########")
    return 1


def audit() -> None:
    log("---- final patch audit ----")
    for m in MODELS:
        p = latest_path(m)
        if not p.exists():
            log(f"{m:32} MISSING")
            continue
        rows = json.loads(p.read_text(encoding="utf-8"))
        patches = sum(1 for t in rows if (t.get("answer") or {}).get("patch"))
        score = sum(int(t.get("score") or 0) for t in rows)
        mx = sum(int(t.get("max_score") or 0) for t in rows)
        flag = "OK" if patches >= 8 else "STALE"
        log(f"{m:32} {score}/{mx} patches={patches}/8 {flag}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    log(
        f"==== cursor repohard stale queue start {now()} "
        f"parallel={PARALLEL} wait_matrix={int(WAIT_MATRIX)} ===="
    )
    st = subprocess.run(["agent", "status"], capture_output=True)
    if st.returncode != 0:
        log("not logged in")
        return 1

    wait_matrix_if_requested()

    # BENCH_FORCE_MODELS=a,b — re-run even if already HAS_PATCH (fresh post-isolation pass).
    force = force_models()
    pending = [m for m in MODELS if m in force or not has_patches(m)]
    for m in MODELS:
        if m not in pending:
            log(f"pre-skip HAS_PATCH {m}")
        elif m in force and has_patches(m):
            log(f"force re-run {m} (already HAD_PATCH)")
    log(f"pending={len(pending)}: {' '.join(pending)}")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    rc = 0
    with ThreadPoolExecutor(max_workers=PARALLEL) as ex:
        futs = {ex.submit(run_one, m): m for m in pending}
        for fut in as_completed(futs):
            m = futs[fut]
            try:
                code = fut.result()
            except Exception as e:
                log(f"worker crashed model={m}: {e!r}")
                code = 1
            rc = rc or code
            log(f"worker finished model={m} rc={code} {now()}")

    audit()
    subprocess.run([PY, "-u", str(ROOT / "run.py"), "report", "--no-color"], cwd=str(ROOT))
    log(f"==== cursor repohard stale queue ALL DONE {now()} ====")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
