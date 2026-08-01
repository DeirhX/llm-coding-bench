#!/usr/bin/env python3
"""Does an edit-discipline rule reduce failed exact-match edits?

WHY THIS EXISTS

In one real Claude Code session on the 31B, 4 of 31 Edit calls failed, and reading the
transcript back gave four different causes rather than one:

  194 lines quoted, 153 matched, then it typed {TAG}___{task.id} for {TAG}__{task.id}
   88 lines quoted at an indentation one level deeper than the file
   33 lines quoted at an indentation one level shallower, mixing two near-identical blocks
    1 line quoted from text its own earlier edit had already replaced

Three of those scale with how much text is quoted, and the fourth does not. A 92-line edit
succeeded immediately after a fresh read, so quoted length alone does not predict failure --
which is why the rule under test says both "quote little" and "read again after changing".

WHAT IT MEASURES

Claude Code's Read hands back ``LINENO<TAB>CONTENT``, so the model must strip a gutter whose
tab sits directly against the file's own leading spaces; guess one level wrong and the result
is the +-4 seen above. This probe reproduces that shape exactly, along with Edit's semantics
and its three error strings, and classifies every failure as one of:

  stale      the quoted text was in the version last read, but not in the file now
  indent     the text matches once leading whitespace is normalised
  content    no match either way, so the characters themselves are wrong
  ambiguous  several matches and replace_all was not set
  unread     edited a file it had never read

The headline number is first-attempt success: edits that landed without a retry. Turn count
and wall time are recorded too, because a failure costs a full round trip.

The fixture is deliberately shaped like the code that provoked the real failures: deep
nesting, two near-identical blocks at different depths, and a long function that invites a
wholesale rewrite. Run --selftest to confirm the tasks are solvable and the graders reject
an unedited file, a check this repo learned to insist on after shipping a trap whose
committed baseline already contained the bug it demanded.
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

HOST = "http://127.0.0.1:11434"
ROOT = Path(__file__).resolve().parent.parent
REPO_FILE = "app/runner.py"

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

# Two result-row builders that differ in one value and sit at different indentation depths,
# a counter nested four levels deep, and a long main() worth rewriting wholesale. Every
# feature here exists because it broke a real edit.
RUNNER_PY = '''"""Batch runner for grading jobs."""

from __future__ import annotations

import json
import time
from pathlib import Path

OUT_DIR = Path("out")
STAMP_FMT = "%Y%m%d_%H%M%S"
MAX_ATTEMPTS = 4


def _row_cursor(job, grade):
    """Build a result row for the cursor provider."""
    return {
        "model": job.model,
        "provider": "cursor",
        "task": job.id,
        "title": job.title,
        "family": job.family,
        "ok": bool(grade.get("ok")),
        "score": int(grade.get("score") or 0),
        "max_score": int(grade.get("max_score") or job.max_score),
        "detail": grade.get("detail"),
    }


def _row_ollama(job, grade):
    """Build a result row for the ollama provider."""
    if job.family:
        return {
            "model": job.model,
            "provider": "ollama",
            "task": job.id,
            "title": job.title,
            "family": job.family,
            "ok": bool(grade.get("ok")),
            "score": int(grade.get("score") or 0),
            "max_score": int(grade.get("max_score") or job.max_score),
            "detail": grade.get("detail"),
        }
    return {}


def build_policy(job, tier):
    """Return the retry policy for one job."""
    policy = {"name": job.id, "tiers": {}}
    for name in ("read", "write"):
        if name == "read":
            policy["tiers"][name] = {
                "backoff": 1.5,
                "retries": 3,
                "jitter": True,
            }
        else:
            if tier == "strict":
                policy["tiers"][name] = {
                    "backoff": 2.0,
                    "retries": 7,
                    "jitter": False,
                }
            else:
                policy["tiers"][name] = {
                    "backoff": 1.0,
                    "retries": 2,
                    "jitter": True,
                }
    return policy


def summarize(rows):
    """Aggregate rows into a single verdict."""
    total = 0
    for row in rows:
        total += int(row.get("score") or 0)
    ceiling = sum(int(r.get("max_score") or 0) for r in rows)
    if ceiling and total / ceiling > 0.75:
        verdict = "pass"
    else:
        verdict = "fail"
    return {"total": total, "ceiling": ceiling, "verdict": verdict}


def main(jobs, provider="ollama", tier="strict"):
    """Run every job, write the results, print a summary."""
    stamp = time.strftime(STAMP_FMT)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_json = OUT_DIR / f"run__{stamp}.json"
    log_path = OUT_DIR / f"run__{stamp}.log"

    rows = []
    failures = []
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"==== run {stamp} provider={provider} ====\\n")
        for index, job in enumerate(jobs, 1):
            log.write(f"-- {job.id}\\n")
            print(f"-- {job.id} ...", flush=True)

            policy = build_policy(job, tier)
            attempts = 0
            grade = {}
            while attempts < MAX_ATTEMPTS:
                attempts += 1
                try:
                    grade = job.run(policy)
                except TimeoutError as exc:
                    log.write(f"   timeout on attempt {attempts}: {exc}\\n")
                    continue
                except RuntimeError as exc:
                    log.write(f"   error on attempt {attempts}: {exc}\\n")
                    failures.append((job.id, str(exc)))
                    break
                if grade.get("ok"):
                    break

            if provider == "cursor":
                row = _row_cursor(job, grade)
            else:
                row = _row_ollama(job, grade)
            row["attempts"] = attempts
            rows.append(row)

            log.write(json.dumps(row) + "\\n")
            out_json.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    summary = summarize(rows)
    summary["failures"] = len(failures)
    summary["stamp"] = stamp
    print("SUMMARY", json.dumps(summary))
    return 0 if summary["verdict"] == "pass" else 1


def format_rows(rows, meta):
    """Render rows as a table. The canonical implementation."""
    if not rows:
        return "(no rows)"
    width = max(len(str(r.get("task") or "")) for r in rows)
    lines = [f"report for {meta.get('model', 'unknown')}"]
    for row in rows:
        name = str(row.get("task") or "?").ljust(width)
        got = int(row.get("score") or 0)
        cap = int(row.get("max_score") or 0)
        pct = (100.0 * got / cap) if cap else 0.0
        flag = "ok" if row.get("ok") else "--"
        lines.append(f"{flag} {name} {got:>4}/{cap:<4} {pct:5.1f}%")
    return "\\n".join(lines)


def render_report(rows, meta):
    """Render rows as a table, the long way round."""
    if not rows:
        return "(no rows)"

    width = 0
    for row in rows:
        name = str(row.get("task") or "")
        if len(name) > width:
            width = len(name)

    header = []
    model_name = meta.get("model")
    if model_name is None:
        model_name = "unknown"
    header.append(f"report for {model_name}")

    body = []
    families = {}
    for row in rows:
        family = row.get("family") or "other"
        if family not in families:
            families[family] = []
        families[family].append(row)

    for family in sorted(families):
        group = families[family]
        group_got = 0
        group_cap = 0
        for row in group:
            score = row.get("score")
            if score is None:
                score = 0
            group_got += int(score)
            cap = row.get("max_score")
            if cap is None:
                cap = 0
            group_cap += int(cap)

        for row in group:
            name = str(row.get("task") or "?")
            padded = name.ljust(width)
            score = row.get("score")
            if score is None:
                score = 0
            got = int(score)
            cap = row.get("max_score")
            if cap is None:
                cap = 0
            cap = int(cap)
            if cap:
                pct = 100.0 * got / cap
            else:
                pct = 0.0
            if row.get("ok"):
                flag = "ok"
            else:
                flag = "--"
            body.append(f"{flag} {padded} {got:>4}/{cap:<4} {pct:5.1f}%")

        if group_cap:
            group_pct = 100.0 * group_got / group_cap
        else:
            group_pct = 0.0
        if len(families) > 1:
            body.append(
                f"   {family}: {group_got}/{group_cap} ({group_pct:.1f}%)"
            )

    warnings = []
    for row in rows:
        if row.get("done_reason") == "task_timeout":
            warnings.append(f"   {row.get('task')} timed out")
        elif not row.get("ok") and not row.get("score"):
            warnings.append(f"   {row.get('task')} scored nothing")

    out = []
    out.extend(header)
    out.extend(body)
    if warnings:
        out.append("warnings:")
        out.extend(warnings)
    return "\\n".join(out)

'''

DOCS_FILE = "README.md"

# A markdown fixture, because every Python line worth quoting is already indented and so a
# quote carrying leading whitespace is usually correct there. Here the target sits at column
# zero, three-space indented prose sits a few lines below it, and the term appears twice more
# inside a fenced block that must not change.
README_MD = """# llm-coding-bench

A harness for scoring model patches against real repositories.

## Adding a bench

1. Create `benches/<name>/` with a `tasks.json` describing each case
2. Add `__main__.py` and register a `BenchSpec` in `benches/registry.py`
3. Run `python -m benches.<name> --list` to confirm the tasks load

Each registry entry carries a module path and a grader:

```python
REGISTRY = {
    "repohard": BenchSpec(module="benches.repohard", grader=grade_patch),
    "pyhard": BenchSpec(module="benches.pyhard", grader=grade_patch),
}
```

## Notes

   Indented prose like this is three spaces deep because the generator emitted it
   that way, and nothing has ever tidied it up.

1. Read the results with `scripts/report.py`
2. Compare two arms with `scripts/compare.py --baseline shipped`
"""

TWIN_FILE = "tools/summarise.py"

# The same function in two files, which is how a wrong-file edit becomes invisible: the
# quote matches in both places, so the tool applies it wherever it was pointed and
# reports success. Attached to the wrong_file task only, so the other tasks keep the
# fixture set the earlier measurement used.
SUMMARISE_PY = '''"""Standalone reporting helper, kept in step with app/runner.py."""

from __future__ import annotations


def format_rows(rows, meta):
    """Render rows as a table. The canonical implementation."""
    if not rows:
        return "(no rows)"
    width = max(len(str(r.get("task") or "")) for r in rows)
    lines = [f"report for {meta.get('model', 'unknown')}"]
    for row in rows:
        name = str(row.get("task") or "?").ljust(width)
        got = int(row.get("score") or 0)
        cap = int(row.get("max_score") or 0)
        lines.append(f"  {name}  {got:>3}/{cap:<3}")
    return "\\n".join(lines)
'''

FILES: dict[str, str] = {REPO_FILE: RUNNER_PY, DOCS_FILE: README_MD}


# ---------------------------------------------------------------------------
# Tasks: instruction plus a verifier over the final file
# ---------------------------------------------------------------------------


def _parses(text: str) -> bool:
    try:
        ast.parse(text)
    except SyntaxError:
        return False
    return True


def _verify_deep_value(text: str) -> tuple[bool, str]:
    if not _parses(text):
        return False, "file no longer parses"
    read_block = re.search(
        r'policy\["tiers"\]\[name\] = \{\n\s+"backoff": 1\.5,\n\s+"retries": (\d+),', text
    )
    if not read_block:
        return False, "read tier block not recognisable"
    if read_block.group(1) != "5":
        return False, f'read tier retries is {read_block.group(1)}, wanted 5'
    if '"retries": 7' not in text or '"retries": 2' not in text:
        return False, "the other two retry values were disturbed"
    return True, "read tier retries 3 -> 5, others intact"


def _verify_twin_blocks(text: str) -> tuple[bool, str]:
    if not _parses(text):
        return False, "file no longer parses"
    cursor = re.search(r"def _row_cursor.*?\n\n\ndef ", text, re.S)
    ollama = re.search(r"def _row_ollama.*?\n\n\ndef ", text, re.S)
    if not cursor or not ollama:
        return False, "could not isolate the two builders"
    if '"task": job.id' not in cursor.group(0):
        return False, "the cursor builder was changed and should not have been"
    if '"id": job.id' not in ollama.group(0):
        return False, "the ollama builder still lacks an id key"
    if '"task": job.id' in ollama.group(0):
        return False, "the ollama builder still has a task key"
    return True, "ollama builder renamed, cursor builder untouched"


def _verify_long_function(text: str) -> tuple[bool, str]:
    if not _parses(text):
        return False, "file no longer parses"
    if 'STAMP_FMT = "%Y-%m-%d_%H%M%S"' not in text:
        return False, "STAMP_FMT not changed to the dashed date form"
    if 'f"run__{stamp}.json"' in text or 'f"run__{stamp}.log"' in text:
        return False, "a double underscore filename remains"
    if 'f"run_{stamp}.json"' not in text or 'f"run_{stamp}.log"' not in text:
        return False, "single underscore filenames not both present"
    # The rest of the long function has to survive the change.
    for sentinel in (
        "while attempts < MAX_ATTEMPTS:",
        "except TimeoutError as exc:",
        'print("SUMMARY", json.dumps(summary))',
    ):
        if sentinel not in text:
            return False, f"lost part of main(): {sentinel!r}"
    return True, "both constants changed, rest of main intact"


def _verify_sequential(text: str) -> tuple[bool, str]:
    if not _parses(text):
        return False, "file no longer parses"
    fn = re.search(r"def summarize.*?\n\n\ndef ", text, re.S)
    if not fn:
        return False, "could not isolate summarize()"
    body = fn.group(0)
    # The dictionary key "total" stays; only the variable was renamed.
    stripped = body.replace("score_total", "").replace('"total"', "")
    if re.search(r"(?<![\w])total(?![\w])", stripped):
        return False, "a bare total variable remains in summarize()"
    if body.count("score_total") < 4:
        return False, f"only {body.count('score_total')} score_total uses, wanted 4"
    if "> 0.9" not in body:
        return False, "threshold not raised to 0.9"
    if '"total": score_total' not in body:
        return False, "returned dict key or value not updated"
    return True, "rename applied and threshold raised on the renamed line"


TASKS: list[dict[str, Any]] = [
    {
        "id": "deep_value",
        "why": "target sits 16 spaces deep, and two near-identical sibling blocks "
               "hold different values for the same key",
        "instruction": (
            "In app/runner.py, the read tier of build_policy retries 3 times. Make it "
            "retry 5 times. Leave the two write tiers exactly as they are."
        ),
        "verify": _verify_deep_value,
    },
    {
        "id": "twin_blocks",
        "why": "two builders differ by one value and sit at different depths, the exact "
               "shape that produced a 33-line mismatch",
        "instruction": (
            "In app/runner.py, the ollama result row uses the key \"task\" for the job "
            "identifier. Rename that key to \"id\" in the ollama builder only. The cursor "
            "builder must keep its \"task\" key."
        ),
        "verify": _verify_twin_blocks,
    },
    {
        "id": "long_function",
        "why": "changes live inside a 60-line function, inviting a wholesale rewrite",
        "instruction": (
            "In app/runner.py, change STAMP_FMT to use a dashed date, \"%Y-%m-%d_%H%M%S\", "
            "and change the two output filenames in main() from a double underscore to a "
            "single one, so run__{stamp}.json becomes run_{stamp}.json. Change nothing "
            "else in main()."
        ),
        "verify": _verify_long_function,
    },
    {
        "id": "sequential",
        "why": "the second change must be quoted against text the first change created",
        "instruction": (
            "In app/runner.py, rename the local variable total to score_total inside "
            "summarize, all four uses including the returned dictionary value. Then raise "
            "the pass threshold on the comparison line from 0.75 to 0.9."
        ),
        "verify": _verify_sequential,
    },
]


# ---------------------------------------------------------------------------
# Claude Code shaped tools
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the repository.",
            "parameters": {
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Replace an exact string in a file. old_string must match the file "
                "byte for byte and must be unique unless replace_all is true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        },
    },
]


def _normalise(text: str) -> str:
    return "\n".join(ln.strip() for ln in text.split("\n") if ln.strip())


class Workspace:
    """The fixture plus Claude Code's Read and Edit semantics.

    Read returns ``LINENO<TAB>CONTENT``. Edit refuses a file that was never read, reports a
    missing or ambiguous match with Claude Code's own wording, and on success returns a
    numbered snippet of the changed region -- which matters, because that snippet is a
    partial refresh of the model's view and therefore part of what it has to work from.
    """

    def __init__(self, extra_files: dict[str, str] | None = None) -> None:
        self.files = copy.deepcopy(FILES)
        if extra_files:
            self.files.update(copy.deepcopy(extra_files))
        self.read_snapshot: dict[str, str] = {}
        self.events: list[dict[str, Any]] = []

    def dispatch(self, name: str, args: dict) -> str:
        if name == "read_file":
            return self._read(str(args.get("file_path") or ""))
        if name == "edit_file":
            return self._edit(args)
        return f"<tool_use_error>Unknown tool: {name}</tool_use_error>"

    def _read(self, path: str) -> str:
        path = path.strip().lstrip("/")
        if path not in self.files:
            self.events.append({"tool": "read", "ok": False, "path": path})
            return f"<tool_use_error>File does not exist: {path}</tool_use_error>"
        text = self.files[path]
        self.read_snapshot[path] = text
        self.events.append({"tool": "read", "ok": True, "path": path})
        return "\n".join(
            f"{i}\t{line}" for i, line in enumerate(text.split("\n"), 1)
        )

    def _edit(self, args: dict) -> str:
        path = str(args.get("file_path") or "").strip().lstrip("/")
        old = args.get("old_string")
        new = args.get("new_string")
        replace_all = bool(args.get("replace_all"))
        old = "" if old is None else str(old)
        new = "" if new is None else str(new)
        event: dict[str, Any] = {
            "tool": "edit",
            "path": path,
            "old_lines": len(old.split("\n")) if old else 0,
            "old_chars": len(old),
            "replace_all": replace_all,
        }

        def finish(ok: bool, fault: str | None, message: str) -> str:
            event["ok"] = ok
            if fault:
                event["fault"] = fault
            self.events.append(event)
            return message

        if path not in self.files:
            return finish(False, "no_such_file",
                          f"<tool_use_error>File does not exist: {path}</tool_use_error>")
        if path not in self.read_snapshot:
            return finish(
                False, "unread",
                "<tool_use_error>File has not been read yet. Read it first before "
                "writing to it.</tool_use_error>",
            )

        text = self.files[path]
        count = text.count(old) if old else 0
        if not old:
            return finish(False, "empty",
                          "<tool_use_error>old_string must not be empty.</tool_use_error>")
        if count == 0:
            # Classify, because the three causes call for different fixes.
            snapshot = self.read_snapshot.get(path, "")
            if old in snapshot:
                fault = "stale"
            elif _normalise(old) and _normalise(old) in _normalise(text):
                fault = "indent"
            else:
                fault = "content"
            return finish(
                False, fault,
                "<tool_use_error>String to replace not found in file. "
                f"String: {old[:200]}</tool_use_error>",
            )
        if count > 1 and not replace_all:
            return finish(
                False, "ambiguous",
                f"<tool_use_error>Found {count} matches of the string to replace, but "
                "replace_all is false. To replace all occurrences, set replace_all to "
                "true. To replace only one occurrence, provide more context to uniquely "
                "identify the instance.</tool_use_error>",
            )

        updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
        self.files[path] = updated
        lines = updated.split("\n")
        head = updated[: updated.find(new)].count("\n") if new and new in updated else 0
        lo = max(0, head - 3)
        hi = min(len(lines), head + len(new.split("\n")) + 3)
        snippet = "\n".join(f"{i}\t{lines[i - 1]}" for i in range(lo + 1, hi + 1))
        return finish(True, None, f"The file {path} has been updated:\n{snippet}")


# ---------------------------------------------------------------------------
# Model loop
# ---------------------------------------------------------------------------


def chat(model: str, messages: list, timeout: int) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "tools": TOOLS,
        "stream": False,
    }
    req = urllib.request.Request(
        f"{HOST}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return json.loads(fh.read())


# ---------------------------------------------------------------------------
# Context padding: dilute the conversation the way a real session does
# ---------------------------------------------------------------------------

_PAD_TOPICS = ["policy", "manifest", "roster", "ledger", "digest", "bundle",
               "shard", "quota"]

_PAD_MODULE = """\"\"\"Helper module @N@: @TOPIC@ handling.\"\"\"

CONFIG_@N@ = {
    "retries": @R@,
    "timeout": @T@,
    "label": "@TOPIC@",
}


def load_@TOPIC@(path, strict=False):
    \"\"\"Read a @TOPIC@ description from disk.\"\"\"
    data = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                if strict:
                    raise ValueError(f"bad line in {path}: {line}")
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
    return data


def merge_@TOPIC@(base, extra):
    \"\"\"Merge two @TOPIC@ mappings, preferring extra.\"\"\"
    out = dict(base)
    for key, value in extra.items():
        if key in out and isinstance(out[key], dict):
            out[key] = merge_@TOPIC@(out[key], value)
        else:
            out[key] = value
    return out


def summarise_@TOPIC@(items, limit=@R@):
    \"\"\"One line per item, name column as wide as the widest name.\"\"\"
    if not items:
        return "(nothing)"
    width = max(len(str(k)) for k in items)
    lines = []
    for key in sorted(items):
        if len(lines) >= limit:
            lines.append(f"... {len(items) - limit} more")
            break
        lines.append(f"{str(key).ljust(width)} {items[key]}")
    return lines
"""


def _pad_module(n: int, topic: str) -> str:
    return (_PAD_MODULE
            .replace("@N@", str(n))
            .replace("@TOPIC@", topic)
            .replace("@R@", str(2 + n % 5))
            .replace("@T@", str(10 * n)))


def _pad_messages(target_tokens: int) -> list[dict[str, Any]]:
    """Prior tool traffic worth roughly target_tokens, in Claude Code's shape."""
    msgs: list[dict[str, Any]] = [{
        "role": "user",
        "content": "Before we start, familiarise yourself with the repository.",
    }]
    used = 0
    n = 0
    while used < target_tokens:
        n += 1
        topic = _PAD_TOPICS[(n - 1) % len(_PAD_TOPICS)]
        src = _pad_module(n, topic)
        numbered = "\n".join(
            f"{i}\t{ln}" for i, ln in enumerate(src.split("\n"), 1)
        )
        msgs.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "function": {
                    "name": "read_file",
                    "arguments": {"file_path": f"app/{topic}_{n:02d}.py"},
                },
            }],
        })
        msgs.append({"role": "tool", "tool_name": "read_file", "content": numbered})
        used += len(numbered) // 4
    msgs.append({
        "role": "assistant",
        "content": f"I have read {n} modules and have a picture of the repository.",
    })
    return msgs


def run_task(
    model: str,
    task: dict[str, Any],
    system: str | None,
    max_rounds: int,
    timeout: int,
    pad_tokens: int = 0,
) -> dict[str, Any]:
    ws = Workspace(task.get("extra_files"))
    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    if pad_tokens:
        messages.extend(_pad_messages(pad_tokens))
    messages.append({
        "role": "user",
        "content": (
            f"{task['instruction']}\n\n"
            "Use the tools to read and change the file. Reply with a one-line summary "
            "when the change is complete."
        ),
    })

    rounds = 0
    tokens = 0
    verdict = "completed"
    started = time.time()
    for rounds in range(1, max_rounds + 1):
        try:
            resp = chat(model, messages, timeout)
        except urllib.error.URLError as exc:
            verdict = "hang" if "timed out" in str(exc).lower() else "error"
            break
        msg = resp.get("message") or {}
        tokens += resp.get("eval_count") or 0
        calls = msg.get("tool_calls") or []
        if resp.get("done_reason") not in ("stop", None):
            verdict = f"unclean:{resp.get('done_reason')}"
            break
        if not calls:
            break
        messages.append(msg)
        for call in calls:
            fn = call.get("function") or {}
            raw = fn.get("arguments") or {}
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError:
                    raw = {}
            messages.append({
                "role": "tool",
                "tool_name": fn.get("name"),
                "content": ws.dispatch(str(fn.get("name") or ""), raw),
            })
    else:
        verdict = "round_cap"

    edits = [e for e in ws.events if e["tool"] == "edit"]
    failed = [e for e in edits if not e.get("ok")]
    target = task.get("file", REPO_FILE)
    ok, detail = task["verify"](ws.files[target])

    # A wrong-file edit succeeds at the tool level when the quote matches in both places, so
    # the untouched file has to be compared against its pristine copy or the fault is invisible.
    pristine = copy.deepcopy(FILES)
    pristine.update(copy.deepcopy(task.get("extra_files") or {}))
    collateral = [
        path for path, before in pristine.items()
        if path != target and ws.files.get(path) != before
    ]
    if collateral and ok:
        ok = False
        detail = f"changed files it was told to leave alone: {collateral}"
    misdirected = [e for e in edits if e.get("path") and e["path"] != target]

    return {
        "task": task["id"],
        "pad_tokens": pad_tokens,
        "verdict": verdict,
        "final_ok": ok,
        "detail": detail,
        "collateral": collateral,
        "misdirected_edits": len(misdirected),
        "rounds": rounds,
        "tokens": tokens,
        "wall_s": round(time.time() - started, 1),
        "reads": sum(1 for e in ws.events if e["tool"] == "read"),
        "edits": len(edits),
        "edit_failures": len(failed),
        "first_attempt_ok": bool(edits) and bool(edits[0].get("ok")),
        "faults": [e.get("fault") for e in failed],
        "old_lines": [e["old_lines"] for e in edits],
        "max_old_lines": max((e["old_lines"] for e in edits), default=0),
    }


# ---------------------------------------------------------------------------
# Selftest: the fixture and the graders, with no model involved
# ---------------------------------------------------------------------------

GOLD: dict[str, Callable[[Workspace], None]] = {
    "deep_value": lambda ws: ws.dispatch("edit_file", {
        "file_path": REPO_FILE,
        "old_string": '                "backoff": 1.5,\n                "retries": 3,',
        "new_string": '                "backoff": 1.5,\n                "retries": 5,',
    }),
    "twin_blocks": lambda ws: ws.dispatch("edit_file", {
        "file_path": REPO_FILE,
        "old_string": '            "provider": "ollama",\n            "task": job.id,',
        "new_string": '            "provider": "ollama",\n            "id": job.id,',
    }),
    "long_function": lambda ws: [
        ws.dispatch("edit_file", {
            "file_path": REPO_FILE,
            "old_string": 'STAMP_FMT = "%Y%m%d_%H%M%S"',
            "new_string": 'STAMP_FMT = "%Y-%m-%d_%H%M%S"',
        }),
        ws.dispatch("edit_file", {
            "file_path": REPO_FILE,
            "old_string": 'f"run__{stamp}.json"',
            "new_string": 'f"run_{stamp}.json"',
        }),
        ws.dispatch("edit_file", {
            "file_path": REPO_FILE,
            "old_string": 'f"run__{stamp}.log"',
            "new_string": 'f"run_{stamp}.log"',
        }),
    ],
    "sequential": lambda ws: [
        ws.dispatch("edit_file", {
            "file_path": REPO_FILE,
            "old_string": "    total = 0\n    for row in rows:\n"
                          "        total += int(row.get(\"score\") or 0)",
            "new_string": "    score_total = 0\n    for row in rows:\n"
                          "        score_total += int(row.get(\"score\") or 0)",
        }),
        ws.dispatch("edit_file", {
            "file_path": REPO_FILE,
            "old_string": "    if ceiling and total / ceiling > 0.75:",
            "new_string": "    if ceiling and score_total / ceiling > 0.9:",
        }),
        ws.dispatch("edit_file", {
            "file_path": REPO_FILE,
            "old_string": 'return {"total": total, "ceiling": ceiling',
            "new_string": 'return {"total": score_total, "ceiling": ceiling',
        }),
    ],
}


# ---------------------------------------------------------------------------
# Refactor tasks: the shape that produced the real failures
# ---------------------------------------------------------------------------

_TWIN_START = "def _row_cursor(job, grade):"
_TWIN_END = "    return {}\n"
_LOOP_START = "            attempts = 0\n"
_LOOP_END = '                if grade.get("ok"):\n                    break\n'
_MAIN_DEF = 'def main(jobs, provider="ollama", tier="strict"):'
_MAIN_BRANCH = (
    '            if provider == "cursor":\n'
    "                row = _row_cursor(job, grade)\n"
    "            else:\n"
    "                row = _row_ollama(job, grade)\n"
)


def _region(start: str, end: str) -> str:
    i = RUNNER_PY.index(start)
    j = RUNNER_PY.index(end, i) + len(end)
    return RUNNER_PY[i:j]


def _unified_row() -> str:
    """The two builders as one, derived from the cursor version's own text."""
    src = _region(_TWIN_START, _TWIN_END)
    cursor = src[: src.index("def _row_ollama")].rstrip("\n")
    cursor = cursor.replace(
        "def _row_cursor(job, grade):", "def _row(job, grade, provider):"
    )
    cursor = cursor.replace(
        '"""Build a result row for the cursor provider."""',
        '"""Build a result row for any provider."""',
    )
    cursor = cursor.replace('"provider": "cursor",', '"provider": provider,')
    return cursor + "\n"


def _retry_helper() -> str:
    """The retry loop lifted to module level, dedented from the fixture's own copy."""
    body = _region(_LOOP_START, _LOOP_END).rstrip("\n")
    dedented = "\n".join(
        ln[8:] if ln.startswith(" " * 8) else ln for ln in body.split("\n")
    )
    return (
        "def run_with_retries(job, policy, log, failures):\n"
        '    """Run one job until it succeeds or the attempt budget runs out."""\n'
        + dedented
        + "\n    return grade, attempts\n"
    )


def _module_funcs(text: str) -> dict[str, ast.FunctionDef]:
    return {
        n.name: n
        for n in ast.parse(text).body
        if isinstance(n, ast.FunctionDef)
    }


def _verify_unify_builders(text: str) -> tuple[bool, str]:
    if not _parses(text):
        return False, "file no longer parses"
    for gone in ("_row_cursor", "_row_ollama"):
        if gone in text:
            return False, f"{gone} is still referenced"
    funcs = _module_funcs(text)
    if "_row" not in funcs:
        return False, "no module-level _row function"
    if len(funcs["_row"].args.args) != 3:
        return False, f"_row takes {len(funcs['_row'].args.args)} arguments, wanted 3"
    if "main" not in funcs:
        return False, "main() disappeared"
    row_src = ast.get_source_segment(text, funcs["_row"]) or ""
    for key in ('"model"', '"provider"', '"task"', '"score"', '"max_score"'):
        if key not in row_src:
            return False, f"the unified row lost {key}"
    main_src = ast.get_source_segment(text, funcs["main"]) or ""
    if "_row(" not in main_src:
        return False, "main() does not call _row"
    for sentinel in ("log.write(json.dumps(row)", 'print("SUMMARY", json.dumps(summary))'):
        if sentinel not in text:
            return False, f"lost part of main(): {sentinel!r}"
    return True, "builders unified, main updated, rest intact"


def _verify_extract_retry(text: str) -> tuple[bool, str]:
    if not _parses(text):
        return False, "file no longer parses"
    funcs = _module_funcs(text)
    if "run_with_retries" not in funcs:
        return False, "no module-level run_with_retries function"
    if "main" not in funcs:
        return False, "main() disappeared"
    helper = ast.get_source_segment(text, funcs["run_with_retries"]) or ""
    main_src = ast.get_source_segment(text, funcs["main"]) or ""
    if "MAX_ATTEMPTS" not in helper:
        return False, "the retry loop is not inside the helper"
    if "while attempts < MAX_ATTEMPTS" in main_src:
        return False, "the loop is still in main()"
    if "run_with_retries(" not in main_src:
        return False, "main() does not call the helper"
    if "return grade, attempts" not in helper.replace("(", "").replace(")", ""):
        return False, "the helper does not return the grade and the attempt count"
    if 'row["attempts"] = attempts' not in main_src:
        return False, "main() lost the attempts column"
    if 'print("SUMMARY", json.dumps(summary))' not in text:
        return False, "lost the summary print"
    return True, "retry loop extracted and called from main"


TASKS.append({
    "id": "unify_builders",
    "why": "replacing two whole functions at once, the shape that produced a "
           "194-line quote in the real session",
    "instruction": (
        "In app/runner.py, _row_cursor and _row_ollama build almost the same "
        "dictionary. Replace both with a single module-level function "
        "_row(job, grade, provider) that always returns the dictionary, taking the "
        "provider name as an argument, and update main() to call it instead of "
        "branching on the provider. Drop the family guard."
    ),
    "verify": _verify_unify_builders,
})

TASKS.append({
    "id": "extract_retry_loop",
    "why": "moving a nested block out of a long function, which forces a quote "
           "spanning the whole loop",
    "instruction": (
        "In app/runner.py, move the retry loop in main() into a new module-level "
        "function run_with_retries(job, policy, log, failures) that returns the "
        "grade and the attempt count as a tuple. That is the attempts and grade "
        "initialisation together with the whole while attempts < MAX_ATTEMPTS "
        "block. Call it from main() in place of the loop, keeping the attempts "
        "column in the row."
    ),
    "verify": _verify_extract_retry,
})

GOLD["unify_builders"] = lambda ws: [
    ws.dispatch("edit_file", {
        "file_path": REPO_FILE,
        "old_string": _region(_TWIN_START, _TWIN_END),
        "new_string": _unified_row(),
    }),
    ws.dispatch("edit_file", {
        "file_path": REPO_FILE,
        "old_string": _MAIN_BRANCH,
        "new_string": "            row = _row(job, grade, provider)\n",
    }),
]

GOLD["extract_retry_loop"] = lambda ws: [
    ws.dispatch("edit_file", {
        "file_path": REPO_FILE,
        "old_string": _MAIN_DEF,
        "new_string": _retry_helper() + "\n\n" + _MAIN_DEF,
    }),
    ws.dispatch("edit_file", {
        "file_path": REPO_FILE,
        "old_string": _region(_LOOP_START, _LOOP_END),
        "new_string": "            grade, attempts = run_with_retries("
                      "job, policy, log, failures)\n",
    }),
]


_LONG_BODY_START = '    """Render rows as a table, the long way round."""'
_LONG_BODY_END = '    return "' + chr(92) + 'n".join(out)' + chr(10)


def _verify_replace_long_body(text: str) -> tuple[bool, str]:
    if not _parses(text):
        return False, "file no longer parses"
    funcs = _module_funcs(text)
    if "render_report" not in funcs:
        return False, "render_report disappeared"
    if "format_rows" not in funcs:
        return False, "format_rows disappeared"
    canonical = ast.get_source_segment(text, funcs["format_rows"]) or ""
    if "families" in canonical or "warnings" in canonical:
        return False, "format_rows was modified"
    body = ast.get_source_segment(text, funcs["render_report"]) or ""
    for gone in ("families", "warnings", "group_cap", "padded"):
        if gone in body:
            return False, f"the long body survives ({gone} still present)"
    if "format_rows(" not in body:
        return False, "render_report does not call format_rows"
    stmts = [n for n in funcs["render_report"].body
             if not isinstance(n, ast.Expr) or not isinstance(n.value, ast.Constant)]
    if len(stmts) != 1 or not isinstance(stmts[0], ast.Return):
        return False, f"render_report has {len(stmts)} statements, wanted one return"
    for sentinel in ('print("SUMMARY", json.dumps(summary))',
                     "while attempts < MAX_ATTEMPTS:"):
        if sentinel not in text:
            return False, f"lost unrelated code: {sentinel!r}"
    return True, "long body replaced by a single delegating call"


TASKS.append({
    "id": "replace_long_body",
    "why": "a 100-plus line body must be replaced by one call, the exact shape "
           "of the 194-line quote that failed in the real session",
    "instruction": (
        "In app/runner.py, render_report reimplements what format_rows already "
        "does. Replace the entire body of render_report with a single line that "
        "returns format_rows(rows, meta). Leave format_rows and everything else "
        "in the file untouched."
    ),
    "verify": _verify_replace_long_body,
})

GOLD["replace_long_body"] = lambda ws: ws.dispatch("edit_file", {
    "file_path": REPO_FILE,
    "old_string": _region(_LONG_BODY_START, _LONG_BODY_END),
    "new_string": '    """Render rows as a table, the long way round."""\n'
                  "    return format_rows(rows, meta)\n",
})


def _verify_markdown_list(text: str) -> tuple[bool, str]:
    lines = text.split("\n")
    target = [ln for ln in lines if "register a" in ln and "registry.py" in ln]
    if len(target) != 1:
        return False, f"expected one registration list item, found {len(target)}"
    line = target[0]
    if "BenchMetadata" not in line:
        return False, "the list item still says BenchSpec"
    # The point of the task: the line begins at column zero and must stay there.
    if line != line.lstrip():
        return False, f"indentation was introduced: {line[:40]!r}"
    if not line.startswith("2. Add"):
        return False, f"the list numbering was disturbed: {line[:40]!r}"
    if text.count("BenchSpec") != 2:
        return False, (f"{text.count('BenchSpec')} BenchSpec left, wanted the 2 inside "
                       "the code block")
    if "BenchMetadata(module=" in text:
        return False, "the fenced code block was rewritten and should not have been"
    for sentinel in ("   Indented prose like this is three spaces deep",
                     "3. Run `python -m benches.<name> --list`"):
        if sentinel not in text:
            return False, f"unrelated content lost: {sentinel[:34]!r}"
    return True, "list item renamed at column zero, code block untouched"


TASKS.append({
    "id": "markdown_list",
    "why": "the target line starts at column zero, the shape that made the model turn "
           "the read's number-and-tab gutter into four spaces of indentation",
    "file": DOCS_FILE,
    "instruction": (
        "In README.md, step 2 of \"Adding a bench\" refers to a `BenchSpec`. That class "
        "is now called `BenchMetadata`, so update that step. Leave the fenced code block "
        "and everything else in the file exactly as it is."
    ),
    "verify": _verify_markdown_list,
})

GOLD["markdown_list"] = lambda ws: ws.dispatch("edit_file", {
    "file_path": DOCS_FILE,
    "old_string": "register a `BenchSpec` in",
    "new_string": "register a `BenchMetadata` in",
})


def _verify_wrong_file(text: str) -> tuple[bool, str]:
    """Given the twin's contents; run_task compares the file it was told not to touch."""
    if not _parses(text):
        return False, "twin no longer parses"
    if "min(24," not in text:
        return False, "the twin does not cap the width at 24"
    if 'width = max(len(str(r.get("task") or "")) for r in rows)' in text:
        return False, "the uncapped width line is still there"
    for sentinel in ('return "(no rows)"', 'name = str(row.get("task") or "?")'):
        if sentinel not in text:
            return False, f"unrelated code in the twin was changed: {sentinel!r}"
    return True, "twin caps the width, rest of it intact"


TASKS.append({
    "id": "wrong_file",
    "file": TWIN_FILE,
    "extra_files": {TWIN_FILE: SUMMARISE_PY},
    "why": "two files hold a byte-identical function, so a quote matches in both and an "
           "edit aimed at the wrong one succeeds silently, as nearly happened live",
    "instruction": (
        "tools/summarise.py and app/runner.py both contain an identical copy of "
        "format_rows. In tools/summarise.py only, cap the task column width at 24 "
        "characters, so a very long task name cannot widen the table beyond that. "
        "app/runner.py must be left exactly as it is."
    ),
    "verify": _verify_wrong_file,
    # The point of the task: the untouched file is as much of the result as the edited one.
    "verify_untouched": REPO_FILE,
})

GOLD["wrong_file"] = lambda ws: ws.dispatch("edit_file", {
    "file_path": TWIN_FILE,
    "old_string": '    width = max(len(str(r.get("task") or "")) for r in rows)',
    "new_string": '    width = min(24, max(len(str(r.get("task") or "")) for r in rows))',
})


def selftest() -> int:
    fails: list[str] = []
    if not _parses(RUNNER_PY):
        fails.append("fixture does not parse")

    for task in TASKS:
        target = task.get("file", REPO_FILE)
        # Unedited fixture must fail the verifier, or the task proves nothing.
        ws = Workspace(task.get("extra_files"))
        ok, detail = task["verify"](ws.files[target])
        if ok:
            fails.append(f"{task['id']}: unedited fixture already passes ({detail})")

        # Gold edits must satisfy it, applied through the real tool path.
        ws = Workspace(task.get("extra_files"))
        ws.dispatch("read_file", {"file_path": target})
        GOLD[task["id"]](ws)
        bad = [e for e in ws.events if e["tool"] == "edit" and not e.get("ok")]
        if bad:
            fails.append(f"{task['id']}: gold edit rejected: {bad}")
        ok, detail = task["verify"](ws.files[target])
        if not ok:
            fails.append(f"{task['id']}: gold edits do not satisfy verifier ({detail})")
        untouched = task.get("verify_untouched")
        if untouched and ws.files[untouched] != copy.deepcopy(FILES)[untouched]:
            fails.append(f"{task['id']}: gold edits disturbed {untouched}")

    # The classifier must name the three causes correctly.
    probes = [
        ("unread", lambda ws: ws.dispatch("edit_file", {
            "file_path": REPO_FILE, "old_string": "MAX_ATTEMPTS = 4", "new_string": "x"})),
        # Two lines, because a single under-indented line still matches as a
        # substring: only a line following a newline must match its indentation.
        ("indent", lambda ws: (
            ws.dispatch("read_file", {"file_path": REPO_FILE}),
            ws.dispatch("edit_file", {
                "file_path": REPO_FILE,
                "old_string": '            "backoff": 1.5,\n            "retries": 3,',
                "new_string": '            "backoff": 1.5,\n            "retries": 5,'}))),
        ("content", lambda ws: (
            ws.dispatch("read_file", {"file_path": REPO_FILE}),
            ws.dispatch("edit_file", {
                "file_path": REPO_FILE,
                "old_string": 'MAX_ATTEMPTS = 44', "new_string": "x"}))),
        ("ambiguous", lambda ws: (
            ws.dispatch("read_file", {"file_path": REPO_FILE}),
            ws.dispatch("edit_file", {
                "file_path": REPO_FILE,
                "old_string": '        "detail": grade.get("detail"),', "new_string": "x"}))),
        ("stale", lambda ws: (
            ws.dispatch("read_file", {"file_path": REPO_FILE}),
            ws.dispatch("edit_file", {
                "file_path": REPO_FILE,
                "old_string": "MAX_ATTEMPTS = 4", "new_string": "MAX_ATTEMPTS = 9"}),
            ws.dispatch("edit_file", {
                "file_path": REPO_FILE,
                "old_string": "MAX_ATTEMPTS = 4", "new_string": "MAX_ATTEMPTS = 5"}))),
    ]
    for expected, act in probes:
        ws = Workspace()
        act(ws)
        got = [e.get("fault") for e in ws.events if e["tool"] == "edit" and not e.get("ok")]
        if expected not in got:
            fails.append(f"classifier: expected {expected}, got {got or 'no failure'}")

    if fails:
        print("SELFTEST FAILED")
        for f in fails:
            print("  " + f)
        return 1
    print(f"SELFTEST OK {json.dumps({'tasks': len(TASKS), 'classes': len(probes)})}")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--model", default="gemma4-31b-mtp-64k")
    ap.add_argument("--arms", nargs="+", default=["control", "rule"],
                    choices=["control", "rule", "skeptic", "skeptic_rule"])
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--max-rounds", type=int, default=14)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", default="results/edit_discipline/probe.json")
    ap.add_argument("--only", nargs="*", help="restrict to these task ids")
    ap.add_argument("--pad-tokens", nargs="+", type=int, default=[0],
                    help="approximate tokens of prior tool traffic to inject")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    rule = (ROOT / "prompts/edit_discipline.md").read_text().strip()
    skeptic = (ROOT / "prompts/skeptic_min.md").read_text().strip()
    systems = {
        "control": None,
        "rule": rule,
        "skeptic": skeptic,
        "skeptic_rule": f"{skeptic}\n\n{rule}",
    }

    tasks = [t for t in TASKS if not args.only or t["id"] in args.only]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for arm in args.arms:
        for pad in args.pad_tokens:
            for rep in range(1, args.repeats + 1):
                for task in tasks:
                    print(f"\n== {arm} pad{pad // 1000}k rep{rep} "
                          f"{task['id']}", flush=True)
                    res = run_task(
                        args.model, task, systems[arm], args.max_rounds,
                        args.timeout, pad,
                    )
                    res["arm"] = arm
                    res["repeat"] = rep
                    res["model"] = args.model
                    results.append(res)
                    print(
                        f"   {'OK ' if res['final_ok'] else 'BAD'} "
                        f"edits={res['edits']} failed={res['edit_failures']} "
                        f"faults={res['faults'] or '-'} maxold={res['max_old_lines']} "
                        f"rounds={res['rounds']} {res['wall_s']}s  {res['detail']}",
                        flush=True,
                    )
                    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n==== summary by arm and padding ====")
    print(f"{'arm':13} {'pad':>6} {'solved':>7} {'edits':>6} {'failed':>7} "
          f"{'1st ok':>7} {'maxold':>7} {'wall_s':>8}  faults")
    for arm in args.arms:
      for pad in args.pad_tokens:
        rows = [r for r in results
                if r["arm"] == arm and r["pad_tokens"] == pad]
        if not rows:
            continue
        edits = sum(r["edits"] for r in rows)
        failed = sum(r["edit_failures"] for r in rows)
        first = sum(1 for r in rows if r["first_attempt_ok"])
        faults: dict[str, int] = {}
        for r in rows:
            for f in r["faults"]:
                faults[f] = faults.get(f, 0) + 1
        print(f"{arm:13} {str(pad // 1000) + 'k':>6} "
              f"{sum(1 for r in rows if r['final_ok']):>4}/{len(rows):<2} "
              f"{edits:>6} {failed:>7} {first:>4}/{len(rows):<2} "
              f"{max(r['max_old_lines'] for r in rows):>7} "
              f"{sum(r['wall_s'] for r in rows):>8.0f}  {faults or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
