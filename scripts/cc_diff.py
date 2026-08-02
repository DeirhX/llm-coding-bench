#!/usr/bin/env python3
"""What a change looks like, read from the diff rather than from what the session says about it.

Two properties of the first real implement run are visible here and nowhere else, because both are
statements about code the session wrote rather than about anything it ran:

- Its tests asserted that a mock had been called with a keyword, with the call under test wrapped in
  ``except Exception: pass``. That is a test of the diff's wiring with the failure path silenced,
  and it passes for any diff of that shape regardless of what the code then does.
- Its change threaded a ``holdings=None`` parameter into three signatures and no caller ever passed
  it, so every production path took the default and behaved exactly as before. The red/green pair
  was real and the change was inert.

Both checks are narrow on purpose, like the rest of the gate's rules: they name a specific shape
that a model reaches for when asked to show a fix and has none. Everything here fails open --
an unreadable diff, a file that will not parse, no git at all -- because a check that cannot see
the change must not be the thing that refuses the answer.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess

# A test file by any of the conventions in use. Used only to decide which half of the diff a hunk
# belongs to; a wrong guess here weakens a check rather than inventing a refusal.
_TEST_PATH = re.compile(r"(^|/)(tests?|spec)/|(^|/)(test_|conftest)|_test\.py$|\.spec\.\w+$")
_ADDED_FILE = re.compile(r"^\+\+\+ b/(?P<path>.+)$")
# Stages are given an absolute scratch directory and told to keep their probes there. One resolved
# that path relative to the repository instead and created `out/scratch/` inside it, so the moment
# untracked files started being read, a throwaway probe became a test this module had opinions
# about. Anything under a directory called scratch is nobody's change.
_SCRATCH = re.compile(r"(^|/)(scratch|\.depth-scratch)(/|$)")
_ADDED_TEST = re.compile(r"^\+\s*(?:async\s+)?def\s+(?P<name>test\w*)\s*\(")
_MOCK_ASSERT = re.compile(r"assert_called|assert_has_calls|assert_any_call|\.call_count|"
                          r"assert_awaited")
# `\bassert\b` alone does not match `cap.assert_called_with(...)`, because the word boundary it
# wants is eaten by the underscore -- so the shape this module exists to catch went unseen.
_ANY_ASSERT = re.compile(r"\bassert\b|\.assert\w*\(|\.should\b|expect\(")
_SWALLOWED = re.compile(r"^\+\s*except\s+[\w.() ,]*:\s*$")
_PASSES = re.compile(r"^\+\s*(pass|\.\.\.)\s*(#.*)?$")


def _git(root: str, *args: str) -> str:
    try:
        out = subprocess.run(("git",) + args, cwd=root, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def diff(root: str) -> str:
    """Everything not yet committed, tracked or not. Empty string when that cannot be determined.

    A plain `git diff HEAD` cannot see a file git has never heard of, and the natural way to add a
    failing test is to write a new file. The checks that read this were built against a change that
    edited an existing test file, so they were silently blind to the commonest shape of the thing
    they exist to catch. Untracked files are added with --intent-to-add inside a temporary index, so
    they appear as ordinary additions and the caller's staging area is left exactly as it was.
    """
    tracked = _git(root, "diff", "HEAD")
    listing = _git(root, "ls-files", "--others", "--exclude-standard")
    fresh = [f for f in listing.split("\n") if f.strip()]
    if not fresh:
        return tracked
    index = os.path.join(root, ".git", "cc_diff_index")
    env = dict(os.environ, GIT_INDEX_FILE=index)
    try:
        subprocess.run(["git", "read-tree", "HEAD"], cwd=root, env=env, capture_output=True,
                       timeout=30)
        subprocess.run(["git", "add", "--intent-to-add", "--"] + fresh, cwd=root, env=env,
                       capture_output=True, timeout=30)
        out = subprocess.run(["git", "diff", "HEAD", "--"] + fresh, cwd=root, env=env,
                             capture_output=True, text=True, timeout=30)
        added = out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        added = ""
    finally:
        try:
            os.unlink(index)
        except OSError:
            pass
    return tracked + added


def _files(text: str):
    """(path, lines) for each file in a unified diff, hunk headers included.

    The headers are kept because git writes the enclosing definition into them, and a parameter
    added to an existing signature is an added line whose ``def`` is context. Dropping them lost
    every case this module was written for.
    """
    path, lines = None, []
    for line in text.splitlines():
        found = _ADDED_FILE.match(line)
        if found:
            if path:
                yield path, lines
            path, lines = found.group("path"), []
        elif path and (line.startswith("@@") or
                       (line.startswith("+") and not line.startswith("+++"))):
            lines.append(line)
    if path:
        yield path, lines


def hollow_tests(text: str) -> list[str]:
    """Added tests whose only assertions are that a mock was called, or that swallow the call.

    Both are read off the added lines alone, so a test that already existed is nobody's business
    here even if it has the same shape: the question is what this change introduced.
    """
    out = []
    for path, added in _files(text):
        if not _TEST_PATH.search(path) or _SCRATCH.search(path):
            continue
        name, asserts, mocky, silenced, pending = None, 0, 0, False, False
        def close() -> None:
            if name and asserts and asserts == mocky:
                out.append("%s in %s asserts only that a mock was called" % (name, path))
            elif name and silenced:
                out.append("%s in %s swallows the exception from the call it is testing" %
                           (name, path))
        for line in added:
            if line.startswith("@@"):
                continue
            found = _ADDED_TEST.match(line)
            if found:
                close()
                name, asserts, mocky, silenced, pending = found.group("name"), 0, 0, False, False
                continue
            if name is None:
                continue
            if pending and _PASSES.match(line):
                silenced = True
            pending = bool(_SWALLOWED.match(line))
            if _ANY_ASSERT.search(line):
                asserts += 1
                if _MOCK_ASSERT.search(line):
                    mocky += 1
        close()
    return out


def _defaults(source: str) -> dict[str, set[str]]:
    """{function: parameters that carry a default}, for one version of one file."""
    out: dict[str, set[str]] = {}
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return out
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        named = [a.arg for a in (args.posonlyargs + args.args)][len(args.args) +
                                                                len(args.posonlyargs) -
                                                                len(args.defaults):]
        named += [a.arg for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None]
        if named:
            out.setdefault(node.name, set()).update(named)
    return out


def _at_head(root: str, path: str) -> str:
    try:
        out = subprocess.run(["git", "show", "HEAD:%s" % path], cwd=root, capture_output=True,
                             text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def _new_parameters(text: str, root: str) -> dict[str, set[str]]:
    """{function: defaulted parameters this change added}, source files only.

    Both versions are parsed rather than the diff read, because a line like
    ``+    holdings: dict | None = None,`` inside a signature and ``+    capacity = f()`` inside a
    body are the same shape to a regex. Matching the diff textually reported four body assignments
    as parameters against two real ones, which is a rule not worth having.
    """
    out: dict[str, set[str]] = {}
    for path, _ in _files(text):
        if _TEST_PATH.search(path) or _SCRATCH.search(path) or not path.endswith(".py"):
            continue
        try:
            with open(os.path.join(root, path), encoding="utf-8", errors="replace") as fh:
                after = _defaults(fh.read())
        except OSError:
            continue
        before = _defaults(_at_head(root, path))
        for function, parameters in after.items():
            fresh = parameters - before.get(function, set())
            if fresh:
                out.setdefault(function, set()).update(fresh)
    return out


def _passed_anywhere(root: str, function: str, parameter: str) -> bool:
    """Does any non-test call of `function` pass `parameter` by keyword?

    Parsed rather than matched, because the call that matters spans four lines in the case this was
    written for. Only files that mention the name are read.
    """
    try:
        found = subprocess.run(["rg", "-l", "--", r"\b%s\s*\(" % re.escape(function), root],
                               capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return True          # cannot look: assume it is used, and say nothing
    for path in found.stdout.split():
        if not path.endswith(".py") or _TEST_PATH.search(os.path.relpath(path, root)):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                tree = ast.parse(fh.read())
        except (OSError, SyntaxError, ValueError):
            return True
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = getattr(target, "id", None) or getattr(target, "attr", None)
            if name != function:
                continue
            if any(kw.arg == parameter for kw in node.keywords):
                return True
    return False


def inert_parameters(text: str, root: str) -> list[str]:
    """Parameters the change added, gave a default, and left every caller ignoring."""
    out = []
    for function, parameters in _new_parameters(text, root).items():
        for parameter in sorted(parameters):
            if not _passed_anywhere(root, function, parameter):
                out.append("%s(%s=...) is never passed by any caller outside the tests"
                           % (function, parameter))
    return out
