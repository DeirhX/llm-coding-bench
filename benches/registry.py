"""Extendable bench registry.

Add a new phase by:
  1. Creating ``benches/<id>/`` with ``bench.py`` exposing ``main() -> int | None``
  2. Registering a ``BenchSpec`` below
  3. (Optional) teaching ``bench_lib.report`` how to summarize its ``*_latest.json``
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable

BENCH_ALIASES: dict[str, str] = {
    "archbench": "arch",
    "py": "pyhard",
    "claims": "claim",
    "repo": "repohard",
    "deepfix": "repohard",
    "audit": "audittrap",
}


@dataclass(frozen=True)
class BenchSpec:
    id: str
    title: str
    summary: str
    module: str
    """Dotted module path whose ``main()`` runs the bench."""
    results_subdir: str = ""
    """Passed to ``bench_lib.paths.results_dir`` (empty = ``results/``)."""
    latest_suffix: str = "_latest.json"
    """Filename suffix used when scanning results (after tag)."""
    expected_tasks: int = 1
    """Task count used when picking the best complete run per model."""

    def load(self) -> Any:
        return import_module(self.module)

    def run(self) -> int:
        mod = self.load()
        main: Callable[[], int | None] = mod.main
        return int(main() or 0)


BENCHES: dict[str, BenchSpec] = {
    "pyhard": BenchSpec(
        id="pyhard",
        title="Pyhard",
        summary="9 Python coding tasks / 99 pts (regex, LRU, alien dict, expr, VM, SAT, patch, unify, SQL)",
        module="benches.pyhard.bench",
        latest_suffix="_pyhard_latest.json",
        expected_tasks=9,
    ),
    "arch": BenchSpec(
        id="arch",
        title="Archbench",
        summary="Tools-first exploration of planted buggy shopapi (9 tasks / 90 pts)",
        module="benches.arch.bench",
        results_subdir="archbench",
        expected_tasks=9,
    ),
    "claim": BenchSpec(
        id="claim",
        title="Claim probe",
        summary="20 true/false traps over shopapi (tie-break / discrimination)",
        module="benches.claim.bench",
        results_subdir="archbench",
        expected_tasks=20,
    ),
    "repohard": BenchSpec(
        id="repohard",
        title="Repohard",
        summary="Large synthetic ledgerkit repo: explore + patch; graded by private pytest (8 tasks)",
        module="benches.repohard.bench",
        results_subdir="repohard",
        expected_tasks=8,
    ),
    "audittrap": BenchSpec(
        id="audittrap",
        title="Audittrap",
        summary="Synthetic miniharness: claim battery + fix/wontfix traps (7 tasks / ~81 pts)",
        module="benches.audittrap.bench",
        results_subdir="audittrap",
        expected_tasks=7,
    ),
}


def normalize_bench_id(bench_id: str) -> str:
    return BENCH_ALIASES.get(bench_id.strip().lower(), bench_id.strip().lower())


def list_benches() -> list[BenchSpec]:
    return list(BENCHES.values())


def get_bench(bench_id: str) -> BenchSpec:
    key = normalize_bench_id(bench_id)
    if key not in BENCHES:
        known = ", ".join(BENCHES)
        raise SystemExit(f"Unknown bench {bench_id!r}. Known: {known}")
    return BENCHES[key]
