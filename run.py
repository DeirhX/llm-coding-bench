#!/usr/bin/env python3.14
"""Unified entrypoint for llm-coding-bench.

Examples:
  python run.py list
  python run.py run pyhard
  python run.py run arch claim
  python run.py run all
  python run.py selftest
  python run.py selftest pyhard
  python run.py report
  python run.py report arch --no-color
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benches.registry import BENCHES, BenchSpec, get_bench, list_benches  # noqa: E402
from bench_lib import report as report_lib  # noqa: E402


def cmd_list(_: argparse.Namespace) -> int:
    print(f"{'ID':<10} {'Title':<14} Module")
    print("-" * 72)
    for b in list_benches():
        print(f"{b.id:<10} {b.title:<14} {b.module}")
        print(f"{'':10} {b.summary}")
        print()
    print("Add a phase: create benches/<id>/bench.py with main(), register in benches/registry.py")
    return 0


def _resolve_ids(names: list[str]) -> list[str]:
    if not names or names == ["all"]:
        return list(BENCHES)
    if len(names) == 1 and "," in names[0]:
        names = [part.strip() for part in names[0].split(",") if part.strip()]
    return [get_bench(name).id for name in names]


def _foreach_bench(
    names: list[str],
    label: str,
    run: Callable[[BenchSpec], int],
    *,
    title: bool = False,
    ok: Callable[[BenchSpec], str] | None = None,
    fail: Callable[[BenchSpec, int], str] | None = None,
) -> int:
    rc = 0
    for bid in _resolve_ids(names):
        spec = get_bench(bid)
        heading = f"{label} {spec.id}"
        if title:
            heading = f"{heading} ({spec.title})"
        print(f"==== {heading} ====", flush=True)
        code = run(spec)
        if code != 0:
            rc = code
            print(fail(spec, code) if fail else f"WARN {spec.id} exited {code}", file=sys.stderr)
        elif ok:
            print(ok(spec), flush=True)
        else:
            print(f"OK {spec.id}", flush=True)
    return rc


def _run_bench(spec: BenchSpec) -> int:
    try:
        return spec.run()
    except SystemExit as exc:
        return int(exc.code or 0) if isinstance(exc.code, int) else 1
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {spec.id}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _selftest_bench(spec: BenchSpec) -> int:
    proc = subprocess.run(
        [sys.executable, "-m", f"benches.{spec.id}"],
        cwd=str(ROOT),
        env={**os.environ, "BENCH_SELFTEST": "1"},
    )
    return proc.returncode


def cmd_run(args: argparse.Namespace) -> int:
    return _foreach_bench(args.benches, "run", _run_bench, title=True)


def cmd_selftest(args: argparse.Namespace) -> int:
    os.environ["BENCH_SELFTEST"] = "1"
    return _foreach_bench(
        args.benches,
        "selftest",
        _selftest_bench,
        ok=lambda spec: f"SELFTEST OK {spec.id}",
        fail=lambda spec, code: f"SELFTEST FAIL {spec.id} rc={code}",
    )


def cmd_report(args: argparse.Namespace) -> int:
    terminal, dest = report_lib.write_report(
        args.bench,
        out_path=Path(args.out) if args.out else None,
        use_color=not args.no_color and sys.stdout.isatty(),
        dedupe=not args.all_runs,
    )
    sys.stdout.write(terminal)
    print(f"Wrote {dest}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run.py",
        description="llm-coding-bench — run, selftest, and report coding/architecture benches",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list", help="List registered benchmark phases")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("run", help="Run one or more benches (default env: BENCH_MODEL, …)")
    sp.add_argument(
        "benches",
        nargs="*",
        default=["all"],
        help="Bench ids or 'all' (default: all)",
    )
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("selftest", help="Run grader/self-tests without a model")
    sp.add_argument("benches", nargs="*", default=["all"], help="Bench ids or 'all'")
    sp.set_defaults(func=cmd_selftest)

    sp = sub.add_parser("report", help="Pretty leaderboard from results/*_latest.json")
    sp.add_argument("bench", nargs="?", default=None, help="Optional bench filter")
    sp.add_argument("--out", default=None, help="Markdown output path (default: results/REPORT.md)")
    sp.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    sp.add_argument(
        "--all-runs",
        action="store_true",
        help="Include smoke/partial/duplicate tags (default: best complete run per model)",
    )
    sp.set_defaults(func=cmd_report)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
