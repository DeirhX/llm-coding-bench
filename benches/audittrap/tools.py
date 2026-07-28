"""Sandbox tools over the miniharness fixture (no network, path-jailed)."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Reuse repohard's battle-tested patch apply (same unified-diff quirks).
from benches.repohard.tools import (  # noqa: E402
    apply_unified_diff,
    extract_patch,
    normalize_patch_text,
)

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixture" / "miniharness"
PRIVATE_ROOT = Path(__file__).resolve().parent / "private"

MAX_LIST_ENTRIES = 200
MAX_READ_BYTES = 48_000
MAX_GREP_HITS = 40
MAX_FIND_REFS = 40


@dataclass
class ToolSession:
    root: Path = FIXTURE_ROOT
    calls: list[dict[str, Any]] = field(default_factory=list)
    files_read: set[str] = field(default_factory=set)
    max_calls: int = 40

    def remaining(self) -> int:
        return max(0, self.max_calls - len(self.calls))

    def _rel(self, path: str) -> Path:
        raw = path.strip().lstrip("./")
        for prefix in (
            "fixture/miniharness/",
            "miniharness/",
            "benches/audittrap/fixture/miniharness/",
        ):
            if raw.startswith(prefix):
                raw = raw[len(prefix) :]
        candidate = (self.root / raw).resolve()
        root = self.root.resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError(f"path escapes fixture root: {path}")
        private = PRIVATE_ROOT.resolve()
        if candidate == private or private in candidate.parents:
            raise ValueError(f"path escapes fixture root: {path}")
        return candidate

    def _rel_str(self, path: Path) -> str:
        return str(path.relative_to(self.root.resolve())).replace("\\", "/")

    def dispatch(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        if self.remaining() <= 0:
            return {"ok": False, "error": "tool call budget exhausted"}
        args = arguments or {}
        try:
            if name == "list_dir":
                result = self.list_dir(str(args.get("path") or "."))
            elif name == "read_file":
                result = self.read_file(str(args.get("path") or ""))
            elif name == "grep":
                result = self.grep(
                    str(args.get("pattern") or ""),
                    str(args.get("path") or "."),
                    bool(args.get("ignore_case") or False),
                )
            elif name == "find_refs":
                result = self.find_refs(str(args.get("symbol") or ""))
            else:
                result = {"ok": False, "error": f"unknown tool: {name}"}
        except Exception as e:  # noqa: BLE001 — surface to model
            result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        self.calls.append({"name": name, "arguments": args, "ok": result.get("ok", False)})
        return result

    def list_dir(self, path: str = ".") -> dict[str, Any]:
        target = self._rel(path)
        if not target.exists():
            return {"ok": False, "error": f"not found: {path}"}
        if not target.is_dir():
            return {"ok": False, "error": f"not a directory: {path}"}
        entries = []
        for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            rel = self._rel_str(p)
            entries.append({"path": rel, "type": "dir" if p.is_dir() else "file"})
            if len(entries) >= MAX_LIST_ENTRIES:
                break
        return {
            "ok": True,
            "path": self._rel_str(target) if target != self.root.resolve() else ".",
            "entries": entries,
        }

    def read_file(self, path: str) -> dict[str, Any]:
        if not path:
            return {"ok": False, "error": "path required"}
        target = self._rel(path)
        if not target.exists() or not target.is_file():
            return {"ok": False, "error": f"not a file: {path}"}
        data = target.read_bytes()
        truncated = False
        if len(data) > MAX_READ_BYTES:
            data = data[:MAX_READ_BYTES]
            truncated = True
        text = data.decode("utf-8", errors="replace")
        rel = self._rel_str(target)
        self.files_read.add(rel)
        lines = text.splitlines()
        numbered = "\n".join(f"{i+1:>4}| {line}" for i, line in enumerate(lines))
        return {
            "ok": True,
            "path": rel,
            "truncated": truncated,
            "content": numbered,
        }

    def grep(self, pattern: str, path: str = ".", ignore_case: bool = False) -> dict[str, Any]:
        if not pattern:
            return {"ok": False, "error": "pattern required"}
        flags = re.MULTILINE
        if ignore_case:
            flags |= re.IGNORECASE
        try:
            rx = re.compile(pattern, flags)
        except re.error as e:
            return {"ok": False, "error": f"invalid regex: {e}"}
        target = self._rel(path)
        if target.is_file():
            files = [target]
        else:
            files = sorted(target.rglob("*.py")) + sorted(target.rglob("*.md"))
        hits = []
        for fp in files:
            try:
                text = fp.read_text(encoding="utf-8")
            except OSError:
                continue
            rel = self._rel_str(fp)
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append({"path": rel, "line": i, "text": line[:240]})
                    if len(hits) >= MAX_GREP_HITS:
                        return {"ok": True, "hits": hits, "truncated": True}
        return {"ok": True, "hits": hits, "truncated": False}

    def find_refs(self, symbol: str) -> dict[str, Any]:
        symbol = (symbol or "").strip()
        if not symbol or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", symbol):
            return {"ok": False, "error": "symbol must be a simple identifier"}
        defs: list[dict[str, Any]] = []
        refs: list[dict[str, Any]] = []
        def_pats = [
            re.compile(rf"^def\s+{re.escape(symbol)}\s*\("),
            re.compile(rf"^class\s+{re.escape(symbol)}\b"),
            re.compile(rf"^{re.escape(symbol)}\s*="),
        ]
        use_pat = re.compile(rf"\b{re.escape(symbol)}\b")
        for fp in sorted(self.root.rglob("*.py")):
            rel = self._rel_str(fp)
            try:
                lines = fp.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                stripped = line.lstrip()
                is_def = any(p.search(stripped) for p in def_pats)
                if is_def:
                    defs.append({"path": rel, "line": i, "text": stripped[:240]})
                elif use_pat.search(line):
                    refs.append({"path": rel, "line": i, "text": stripped[:240]})
                if len(defs) + len(refs) >= MAX_FIND_REFS * 2:
                    break
        return {
            "ok": True,
            "symbol": symbol,
            "definitions": defs[:MAX_FIND_REFS],
            "references": refs[:MAX_FIND_REFS],
        }


def fresh_fixture_copy() -> Path:
    td = Path(tempfile.mkdtemp(prefix="audittrap_"))
    dest = td / "miniharness"
    shutil.copytree(FIXTURE_ROOT, dest)
    return dest


__all__ = [
    "FIXTURE_ROOT",
    "PRIVATE_ROOT",
    "ToolSession",
    "apply_unified_diff",
    "extract_patch",
    "fresh_fixture_copy",
    "normalize_patch_text",
]
