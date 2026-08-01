#!/usr/bin/env python3
"""Offline checks for the evidence recorder and the citation verifier (no model, no network).

The fixture is the real thing: the five citations the 31B produced in the depth-gate spike, three
byte-exact and two reindented, cited against files in this repository. A verifier that cannot tell
those apart from an invented quote is not worth running.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))

import cc_evidence as ev  # noqa: E402
import cc_verify as vf  # noqa: E402

ROOT = str(_REPO)


def test_exact_quote_passes() -> None:
    src = (_REPO / "scripts" / "cc_verify.py").read_text().split("\n")
    quote = "\n".join(src[9:13])
    v = vf.file_quote(ROOT, "scripts/cc_verify.py", 10, 13, quote)
    assert v.kind == vf.PASS, v
    assert v.ok


def test_reindented_quote_is_flagged_but_accepted() -> None:
    src = (_REPO / "scripts" / "cc_verify.py").read_text().split("\n")
    quote = "\n".join("    " + line for line in src[9:13])
    v = vf.file_quote(ROOT, "scripts/cc_verify.py", 10, 13, quote)
    assert v.kind == vf.INDENT_DRIFT, v
    assert v.ok, "content was right; drift must not fail the claim"
    assert "+4" in v.detail, v.detail


def test_retouched_quote_is_named_not_called_fabrication() -> None:
    """One line's whitespace altered: the spike's actual failure, and not an invented quote."""
    src = (_REPO / "scripts" / "cc_verify.py").read_text().split("\n")
    lines = src[9:13]
    quote = "\n".join([lines[0]] + [" " + l for l in lines[1:]])
    v = vf.file_quote(ROOT, "scripts/cc_verify.py", 10, 13, quote)
    assert v.kind == vf.RETOUCHED, v
    assert not v.ok, "an edited quote must still be re-read"
    assert "whitespace altered" in v.detail, v.detail


def test_invented_quote_fails() -> None:
    v = vf.file_quote(ROOT, "scripts/cc_verify.py", 10, 13,
                      "def totally_fabricated():\n    return 'nope'")
    assert v.kind == vf.FAIL, v
    assert not v.ok


def test_right_text_wrong_lines_is_distinguished() -> None:
    src = (_REPO / "scripts" / "cc_verify.py").read_text().split("\n")
    quote = "\n".join(src[9:13])
    v = vf.file_quote(ROOT, "scripts/cc_verify.py", 400, 404, quote)
    assert v.kind in (vf.WRONG_LINES, vf.FAIL), v
    assert not v.ok


def test_missing_file_is_unverified_not_pass() -> None:
    v = vf.file_quote(ROOT, "scripts/does_not_exist.py", 1, 2, "anything")
    assert v.kind == vf.UNVERIFIED, v
    assert not v.ok


def test_absence_distinguishes_present_from_absent() -> None:
    # Assembled at runtime: a literal here would be found in this file and defeat the check.
    missing = "junit" + "xml"
    gone = vf.absence(ROOT, missing, "*.py")
    assert gone.kind == vf.PASS, gone
    here = vf.absence(ROOT, "def file_quote", "*.py")
    assert here.kind == vf.FAIL, here


def test_log_match() -> None:
    ok = vf.log_match(str(_REPO / "scripts" / "cc_verify.py"), r"^def file_quote")
    assert ok.kind == vf.PASS, ok
    no = vf.log_match(str(_REPO / "scripts" / "cc_verify.py"), r"^def nothing_like_this")
    assert no.kind == vf.FAIL, no
    absent = vf.log_match("/tmp/no-such-log-here.log", "x")
    assert absent.kind == vf.UNVERIFIED, absent


def test_ledger_parsing_keeps_unknowns_and_malformed_claims() -> None:
    text = (
        "CLAIM: something true\n"
        "EVIDENCE: scripts/cc_verify.py:10-13\n"
        "QUOTE:\n"
        "whatever\n\n"
        "CLAIM: no evidence at all\n\n"
        "UNKNOWN: could not establish the filename\n"
    )
    claims, unknowns = vf.parse_ledger(text)
    assert len(claims) == 2, claims
    assert claims[1]["path"] is None
    assert unknowns == ["could not establish the filename"]
    results = vf.verify_ledger(ROOT, text)
    assert results[1][1].kind == vf.UNVERIFIED, results[1][1]


def test_recorder_reads_failures_and_subagents() -> None:
    """Against a real session: failures must be present, and subagent calls must be included."""
    import glob
    import os
    pattern = os.path.expanduser(
        "~/.claude/projects/-Users-deirh-Projects-llm-coding-bench/*/subagents/agent-*.jsonl")
    subs = glob.glob(pattern)
    if not subs:
        print("  (skipped recorder check: no session with subagents on this machine)")
        return
    session = os.path.dirname(os.path.dirname(subs[0])) + ".jsonl"
    calls = ev.collect(session)
    assert calls, "no tool calls recovered"
    agents = {c.agent for c in calls}
    assert agents - {"parent"}, "subagent transcripts were not read: %s" % agents
    fails = ev.failures(calls)
    assert fails, "session had no failures to find"
    assert all(f.text for f in fails), "a failure was recorded without its error text"


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  %s" % t.__name__)
    print("\n%d checks passed" % len(tests))
    return 0


if __name__ == "__main__":
    sys.exit(main())
