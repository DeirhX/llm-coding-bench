"""Sandbox tools over the ledgerkit fixture (no network, path-jailed)."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixture" / "ledgerkit"
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
            "fixture/ledgerkit/",
            "ledgerkit/",
            "benches/repohard/fixture/ledgerkit/",
        ):
            if raw.startswith(prefix):
                raw = raw[len(prefix) :]
        candidate = (self.root / raw).resolve()
        root = self.root.resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError(f"path escapes fixture root: {path}")
        # never allow private via jail
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
                    hits.append({"path": rel, "line": i, "text": line.rstrip()[:240]})
                    if len(hits) >= MAX_GREP_HITS:
                        return {"ok": True, "hits": hits, "truncated": True}
        return {"ok": True, "hits": hits, "truncated": False}

    def find_refs(self, symbol: str) -> dict[str, Any]:
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


TOOL_SPECS = [
    {
        "name": "list_dir",
        "description": "List files and directories under a path relative to the ledgerkit repo root.",
        "parameters": {"path": "string, default '.'"},
    },
    {
        "name": "read_file",
        "description": "Read a text file with line numbers. Path relative to ledgerkit root.",
        "parameters": {"path": "string, required"},
    },
    {
        "name": "grep",
        "description": "Regex search across .py/.md files under path (default repo root).",
        "parameters": {
            "pattern": "string, required",
            "path": "string, optional",
            "ignore_case": "bool, optional",
        },
    },
    {
        "name": "find_refs",
        "description": "Find definitions and references for a Python symbol name.",
        "parameters": {"symbol": "string, required"},
    },
]


def extract_patch(answer: dict[str, Any] | str | None) -> str:
    """Pull a unified diff from a final-answer object or raw string.

    Do not ``.strip()`` the diff body — trailing blank context lines are part of
    the hunk and ``git apply`` will call the patch corrupt without them.
    """
    if answer is None:
        return ""
    if isinstance(answer, str):
        text = answer
    else:
        text = ""
        for key in ("patch", "diff", "unified_diff"):
            val = answer.get(key)
            if isinstance(val, str) and val.strip():
                text = val
                break
        else:
            text = str(answer.get("content") or "")
    text = normalize_patch_text(text)
    return text


def normalize_patch_text(text: str) -> str:
    """Clean common model patch quirks (fences, literal \\n, CRLF)."""
    text = text.lstrip("\ufeff")
    if text.startswith("```"):
        text = re.sub(r"^```(?:diff|patch|json)?\s*\n?", "", text, flags=re.I)
        text = re.sub(r"\n?```\s*$", "", text)
    # JSON-in-string accidents: entire patch as one line with literal \n
    if "\\n" in text and text.count("\n") < 3 and ("---" in text or "+++" in text):
        text = (
            text.replace("\\r\\n", "\n")
            .replace("\\n", "\n")
            .replace("\\t", "\t")
            .replace('\\"', '"')
        )
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("\n"):
        text = text[1:]
    if not text.endswith("\n"):
        text += "\n"
    return text


_HUNK_RE = re.compile(
    r"^@@(?: -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@|(?:\s*@@)?)\s*(.*)?$"
)


def _strip_diff_prefixes(path: str) -> str:
    path = path.strip()
    if path.startswith(("a/", "b/")):
        path = path[2:]
    if path in ("/dev/null", "dev/null"):
        return path
    return path.lstrip("./")


def apply_hunks_fuzzy(work: Path, patch_text: str, *, fuzz: int = 40) -> dict[str, Any]:
    """Best-effort unified-diff apply when git apply rejects context drift.

    Skips hunks that cannot be located (common when models also touch public
    tests) as long as at least one hunk lands.
    """
    body = normalize_patch_text(patch_text)
    lines = body.splitlines()
    i = 0
    touched = 0
    skipped: list[str] = []
    while i < len(lines):
        line = lines[i]
        if not line.startswith("--- "):
            i += 1
            continue
        old_path = _strip_diff_prefixes(line[4:].split("\t")[0])
        if i + 1 >= len(lines) or not lines[i + 1].startswith("+++ "):
            return {"ok": False, "detail": "fuzzy: missing +++ line"}
        new_path = _strip_diff_prefixes(lines[i + 1][4:].split("\t")[0])
        i += 2
        target_rel = new_path if new_path not in ("/dev/null", "dev/null") else old_path
        target = work / target_rel
        if old_path in ("/dev/null", "dev/null"):
            current: list[str] = []
        else:
            src = work / old_path
            if not src.is_file():
                skipped.append(f"missing:{old_path}")
                while i < len(lines) and not lines[i].startswith("--- "):
                    i += 1
                continue
            current = src.read_text(encoding="utf-8").splitlines()
        file_touched = 0
        while i < len(lines) and lines[i].startswith("@@"):
            m = _HUNK_RE.match(lines[i])
            if not m:
                skipped.append(f"bad-hunk:{target_rel}")
                i += 1
                break
            # Models sometimes emit bare @@ with no line numbers.
            old_start = int(m.group(1) or 1)
            i += 1
            old_block: list[str] = []
            new_block: list[str] = []
            while i < len(lines):
                hl = lines[i]
                if hl.startswith("@@") or hl.startswith("--- "):
                    break
                if hl.startswith("\\"):
                    i += 1
                    continue
                if not hl:
                    old_block.append("")
                    new_block.append("")
                    i += 1
                    continue
                tag, rest = hl[0], hl[1:]
                if tag == " ":
                    old_block.append(rest)
                    new_block.append(rest)
                elif tag == "-":
                    old_block.append(rest)
                elif tag == "+":
                    new_block.append(rest)
                else:
                    old_block.append(hl)
                    new_block.append(hl)
                i += 1
            idx = _find_block(current, old_block, preferred=max(0, old_start - 1), fuzz=fuzz)
            if idx is None:
                skipped.append(f"{target_rel}@{old_start}")
                continue
            current = current[:idx] + new_block + current[idx + len(old_block) :]
            file_touched += 1
            touched += 1
        if file_touched:
            target.parent.mkdir(parents=True, exist_ok=True)
            text = "\n".join(current)
            if current:
                text += "\n"
            target.write_text(text, encoding="utf-8", newline="\n")
    if touched == 0:
        return {
            "ok": False,
            "detail": f"fuzzy: no hunks applied (skipped={skipped[:6]})",
        }
    detail = f"fuzzy applied {touched} hunks"
    if skipped:
        detail += f" skipped={len(skipped)}"
    return {"ok": True, "detail": detail}


def _find_block(
    haystack: list[str], needle: list[str], *, preferred: int, fuzz: int
) -> int | None:
    if not needle:
        return min(preferred, len(haystack))
    # exact at preferred
    if preferred >= 0 and preferred + len(needle) <= len(haystack):
        if haystack[preferred : preferred + len(needle)] == needle:
            return preferred
    # scan near preferred then full file
    windows: list[int] = []
    for delta in range(0, fuzz + 1):
        for pos in (preferred - delta, preferred + delta):
            if pos >= 0 and pos + len(needle) <= len(haystack):
                windows.append(pos)
    for pos in range(0, max(0, len(haystack) - len(needle) + 1)):
        windows.append(pos)
    seen: set[int] = set()
    for pos in windows:
        if pos in seen:
            continue
        seen.add(pos)
        if haystack[pos : pos + len(needle)] == needle:
            return pos
    return None


def apply_unified_diff(work: Path, patch_text: str) -> dict[str, Any]:
    """Apply a unified diff to a workspace copy. Returns {ok, detail}."""
    if not patch_text.strip():
        return {"ok": False, "detail": "empty patch"}
    body = normalize_patch_text(patch_text)
    patch_file = work / ".repohard_agent.patch"
    patch_file.write_text(body, encoding="utf-8", newline="\n")
    errors: list[str] = []
    for args in (
        ["git", "apply", "--whitespace=nowarn", "-p1", str(patch_file)],
        ["git", "apply", "--whitespace=nowarn", "-p0", str(patch_file)],
    ):
        r = subprocess.run(args, cwd=str(work), capture_output=True, text=True)
        if r.returncode == 0:
            return {"ok": True, "detail": f"applied via {' '.join(args[2:4])}"}
        errors.append((r.stderr or r.stdout or "").strip())
    # Fuzzy on pristine tree (git apply is atomic; do not use --reject).
    fuzzy = apply_hunks_fuzzy(work, body)
    if fuzzy["ok"]:
        return fuzzy
    detail = errors[-1] if errors else ""
    return {
        "ok": False,
        "detail": f"git apply failed: {detail}; {fuzzy.get('detail')}",
    }


def fresh_fixture_copy() -> Path:
    td = Path(tempfile.mkdtemp(prefix="repohard_"))
    dest = td / "ledgerkit"
    shutil.copytree(FIXTURE_ROOT, dest)
    return dest
