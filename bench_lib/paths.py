from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def results_dir(*parts: str) -> Path:
    """Default to <repo>/results/...; override with BENCH_OUT."""
    base = Path(os.environ.get("BENCH_OUT", REPO_ROOT / "results"))
    path = base.joinpath(*parts) if parts else base
    path.mkdir(parents=True, exist_ok=True)
    return path
