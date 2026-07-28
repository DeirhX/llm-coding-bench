"""Path helpers."""

from __future__ import annotations

from pathlib import Path


def rel_to_repo(path: Path, root: Path) -> str:
    if path.is_relative_to(root):
        return str(path.relative_to(root))
    return str(path)
