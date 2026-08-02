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



def test_a_citation_in_backticks_is_still_a_citation() -> None:
    """A whole review came back empty over this. The model writes paths as code, as anyone would."""
    claims, _ = vf.parse_ledger("CLAIM: x\nEVIDENCE: `a/b.py:10-12`\nQUOTE:\n    pass\n")
    ev = claims[0]["evidence"]
    assert [e["kind"] for e in ev] == ["file_quote"], ev
    assert (ev[0]["path"], ev[0]["start"], ev[0]["end"]) == ("a/b.py", 10, 12), ev


def test_two_citations_on_one_line_are_both_kept() -> None:
    claims, _ = vf.parse_ledger(
        "CLAIM: x\nEVIDENCE: `a/b.py:10-12` and `a/b.py:40-41`\nQUOTE:\n    pass\n")
    ev = claims[0]["evidence"]
    assert [(e["start"], e["end"]) for e in ev] == [(10, 12), (40, 41)], ev
    assert ev[-1].get("quote") == "    pass", "the quote belongs to the citation it sits under"


def test_a_single_line_citation_is_a_citation() -> None:
    claims, _ = vf.parse_ledger("CLAIM: x\nEVIDENCE: a/b.py:48\nQUOTE:\n    pass\n")
    ev = claims[0]["evidence"]
    assert [(e["start"], e["end"]) for e in ev] == [(48, 48)], ev


def test_ranges_after_a_path_belong_to_that_path() -> None:
    """Written by the model as `depth_pipeline.py`:48, 345-348, and refused for citing nothing."""
    claims, _ = vf.parse_ledger(
        "CLAIM: x\nEVIDENCE: `s/depth_pipeline.py`:48, 345-348\nQUOTE:\n    pass\n")
    ev = claims[0]["evidence"]
    assert [(e["path"], e["start"], e["end"]) for e in ev] == \
[("s/depth_pipeline.py", 48, 48), ("s/depth_pipeline.py", 345, 348)], ev


def test_a_command_is_not_split_on_its_punctuation() -> None:
    claims, _ = vf.parse_ledger(
        "CLAIM: x\nEVIDENCE: command: rg -n 'a:1-2' src && echo done -> done\n")
    ev = claims[0]["evidence"]
    assert [e["kind"] for e in ev] == ["command_result"], ev
    assert ev[0]["command"] == "rg -n 'a:1-2' src && echo done", ev


def test_an_absence_pattern_shaped_like_a_citation_stays_an_absence() -> None:
    claims, _ = vf.parse_ledger("CLAIM: x\nEVIDENCE: absence: foo:1-2 in *.py\n")
    assert [e["kind"] for e in claims[0]["evidence"]] == ["absence"], claims[0]["evidence"]

if __name__ == "__main__":
    sys.exit(main())


def test_a_fenced_quote_is_not_a_fabrication(tmp_path):
    """The 31B wraps quotes in ```python. Verbatim content inside a fence must still pass.

    Regression from the false-premise arm: the fence lines were compared as content, the quote
    "was not present", and a correct citation was reported as the thing the gate exists to catch.
    """
    src = tmp_path / "report.py"
    src.write_text("import os\n\n\ndef write_report():\n    return 1\n")
    fenced = "```python\ndef write_report():\n    return 1\n```"
    assert vf.file_quote(str(tmp_path), "report.py", 4, 5, fenced).kind == vf.PASS


def test_an_interior_fence_is_still_content(tmp_path):
    """Only a fence wrapping the whole quote is dropped; markdown being quoted stays intact."""
    src = tmp_path / "doc.md"
    src.write_text("intro\n```sh\nls -l\n```\ntail\n")
    assert vf.file_quote(str(tmp_path), "doc.md", 1, 5,
                                "intro\n```sh\nls -l\n```\ntail").kind == vf.PASS