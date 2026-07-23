"""Load bench assignments from markdown / simple YAML (stdlib only)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.S)


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    m = _FRONT_MATTER.match(text.strip() + ("\n" if not text.endswith("\n") else ""))
    if not m:
        # tolerate missing trailing newline
        m = _FRONT_MATTER.match(text.strip())
    if not m:
        return {}, text.strip() + "\n"
    meta_raw, body = m.group(1), m.group(2)
    meta: dict[str, Any] = {}
    for line in meta_raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            meta[key] = [x.strip() for x in inner.split(",") if x.strip()] if inner else []
        elif re.fullmatch(r"-?\d+", val):
            meta[key] = int(val)
        elif val.lower() in ("true", "false"):
            meta[key] = val.lower() == "true"
        else:
            meta[key] = val.strip("\"'")
    return meta, body.lstrip("\n")


def load_markdown_assignment(path: Path) -> tuple[dict[str, Any], str]:
    return parse_front_matter(path.read_text(encoding="utf-8"))


def load_simple_claims_yaml(path: Path) -> list[dict[str, Any]]:
    """Parse the tiny claims.yaml subset used by claim bench (no PyYAML)."""
    claims: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "claims:":
            continue
        if stripped.startswith("- id:"):
            if cur:
                claims.append(cur)
            cur = {"id": stripped.split(":", 1)[1].strip()}
            continue
        if cur is None:
            continue
        if stripped.startswith("text:"):
            val = stripped.split(":", 1)[1].strip()
            if val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            cur["text"] = val
        elif stripped.startswith("gold:"):
            cur["gold"] = stripped.split(":", 1)[1].strip().lower() == "true"
        elif stripped.startswith("difficulty:"):
            cur["difficulty"] = stripped.split(":", 1)[1].strip()
    if cur:
        claims.append(cur)
    return claims
