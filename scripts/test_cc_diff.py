#!/usr/bin/env python3
"""Checks on reading a change: real git repositories, no model, no network.

Each case builds a small repository, commits a before, edits an after, and asks the module what it
sees. Driving real git rather than hand-written diff text is deliberate: two of the three bugs found
while writing this were about what git actually emits -- the enclosing ``def`` living in the hunk
header, and a body assignment being indistinguishable from a parameter to a regex.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cc_diff  # noqa: E402

BEFORE = '''def capacity(holdings=None):
    return {"cash": 1}


def prepare(account, basket):
    return capacity()


def caller():
    return prepare("acc", [])
'''


def _repo(tmp: str, after: str, tests: str = "", new_file: tuple = ()) -> str:
    root = Path(tmp)
    (root / "src").mkdir()
    (root / "src/m.py").write_text(BEFORE)
    (root / "tests").mkdir()
    (root / "tests/test_m.py").write_text("import src.m\n")
    run = lambda *a: subprocess.run(a, cwd=str(root), capture_output=True, check=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "before")
    (root / "src/m.py").write_text(after)
    if tests:
        (root / "tests/test_m.py").write_text("import src.m\n" + tests)
    if new_file:
        (root / new_file[0]).write_text(new_file[1])
    return str(root)


def test_a_parameter_nobody_passes_is_named() -> None:
    after = BEFORE.replace("def prepare(account, basket):\n    return capacity()",
                           "def prepare(account, basket, holdings=None):\n"
                           "    return capacity(holdings=holdings)")
    with tempfile.TemporaryDirectory() as tmp:
        root = _repo(tmp, after)
        found = cc_diff.inert_parameters(cc_diff.diff(root), root)
    assert found == ["prepare(holdings=...) is never passed by any caller outside the tests"], found


def test_a_parameter_the_caller_passes_is_not_reported() -> None:
    """The same change, finished: the caller was updated too."""
    after = BEFORE.replace("def prepare(account, basket):\n    return capacity()",
                           "def prepare(account, basket, holdings=None):\n"
                           "    return capacity(holdings=holdings)")
    after = after.replace('return prepare("acc", [])', 'return prepare("acc", [], holdings={})')
    with tempfile.TemporaryDirectory() as tmp:
        root = _repo(tmp, after)
        assert cc_diff.inert_parameters(cc_diff.diff(root), root) == []


def test_an_assignment_in_a_body_is_not_a_parameter() -> None:
    """Read off the diff textually, ``+    capacity = f()`` and a defaulted parameter are one shape.

    The first version of this module reported four body assignments against two real parameters,
    which is a rule that costs more than it catches.
    """
    after = BEFORE.replace("    return capacity()",
                           "    put_cash = capacity()\n    available = 1\n    return put_cash")
    with tempfile.TemporaryDirectory() as tmp:
        root = _repo(tmp, after)
        assert cc_diff._new_parameters(cc_diff.diff(root), root) == {}


def test_a_test_that_only_checks_a_mock_was_called_is_named() -> None:
    tests = '''

def test_prepare_forwards_holdings():
    with mock.patch("src.m.capacity") as cap:
        src.m.prepare("acc", [], holdings={})
        cap.assert_called_with(holdings={})
'''
    with tempfile.TemporaryDirectory() as tmp:
        root = _repo(tmp, BEFORE, tests)
        found = cc_diff.hollow_tests(cc_diff.diff(root))
    assert found and "asserts only that a mock was called" in found[0], found


def test_a_test_that_swallows_the_call_is_named() -> None:
    tests = '''

def test_prepare_forwards_holdings():
    try:
        src.m.prepare("acc", [], holdings={})
    except Exception:
        pass
    assert True
'''
    with tempfile.TemporaryDirectory() as tmp:
        root = _repo(tmp, BEFORE, tests)
        found = cc_diff.hollow_tests(cc_diff.diff(root))
    assert found and "swallows the exception" in found[0], found


def test_a_test_that_asserts_a_value_is_left_alone() -> None:
    tests = '''

def test_capacity_uses_the_holdings_it_is_given():
    assert src.m.capacity({"cash": 0})["cash"] == 0
'''
    with tempfile.TemporaryDirectory() as tmp:
        root = _repo(tmp, BEFORE, tests)
        assert cc_diff.hollow_tests(cc_diff.diff(root)) == []


def test_no_git_is_silence_rather_than_a_refusal() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        assert cc_diff.diff(tmp) == ""
        assert cc_diff.hollow_tests("") == []
        assert cc_diff.inert_parameters("", tmp) == []

FRESH_TEST = (
    "import src.m\n"
    "from unittest import mock\n"
    "\n"
    "\n"
    "def test_prepare_forwards_holdings():\n"
    "    with mock.patch('src.m.capacity') as cap:\n"
    "        src.m.prepare('acc', [], holdings={})\n"
    "        cap.assert_called_with(holdings={})\n"
)


def test_a_test_file_git_has_never_seen_is_still_read() -> None:
    """`git diff HEAD` cannot see an untracked file, and a new test is usually a new file.

    The checks that read the diff were written against a change that edited an existing test
    file, so the commonest shape of the thing they exist to catch went straight past them.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _repo(tmp, BEFORE, new_file=("tests/test_new.py", FRESH_TEST))
        found = cc_diff.hollow_tests(cc_diff.diff(root))
    assert found and "test_new.py" in found[0], found


def test_reading_the_diff_leaves_the_index_alone() -> None:
    """The intent-to-add happens in a throwaway index; a caller mid-commit must not notice."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _repo(tmp, BEFORE, new_file=("tests/test_new.py", "x = 1\n"))
        args = ["git", "status", "--porcelain"]
        before = subprocess.run(args, cwd=root, capture_output=True, text=True).stdout
        cc_diff.diff(root)
        after = subprocess.run(args, cwd=root, capture_output=True, text=True).stdout
    assert before == after, (before, after)
    assert "??" in before, before

def test_a_probe_left_in_scratch_is_not_a_test_of_the_change() -> None:
    """A stage told to keep its probes in an absolute scratch path put them in ./out/scratch.

    Harmless until untracked files started being read, at which point a throwaway probe became
    something this module had opinions about.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _repo(tmp, BEFORE)
        (pathlib.Path(root) / "out/scratch").mkdir(parents=True)
        (pathlib.Path(root) / "out/scratch/test_probe.py").write_text(FRESH_TEST)
        assert cc_diff.hollow_tests(cc_diff.diff(root)) == []

def test_untracked_files_are_read_in_a_linked_worktree() -> None:
    """In a linked worktree .git is a file, not a directory.

    Every review of another repository here runs in one, so assuming root/.git meant the
    temporary index path was invalid, every git call failed quietly, and untracked files went
    unread in exactly the setting the check was written for.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _repo(tmp, BEFORE)
        linked = os.path.join(tmp, "linked")
        subprocess.run(["git", "worktree", "add", "--detach", linked], cwd=root,
                       capture_output=True, check=True)
        assert os.path.isfile(os.path.join(linked, ".git")), "not a linked worktree"
        open(os.path.join(linked, "tests/test_new.py"), "w").write(FRESH_TEST)
        found = cc_diff.hollow_tests(cc_diff.diff(linked))
    assert found and "test_new.py" in found[0], found
