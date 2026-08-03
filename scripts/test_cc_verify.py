#!/usr/bin/env python3
"""Offline checks for the evidence recorder and the citation verifier (no model, no network).

The fixture is the real thing: the five citations the 31B produced in the depth-gate spike, three
byte-exact and two reindented, cited against files in this repository. A verifier that cannot tell
those apart from an invented quote is not worth running.
"""

from __future__ import annotations

import pathlib
import sys
import subprocess
import tempfile
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

def test_a_quote_with_the_line_numbers_still_on_it_is_accepted(tmp_path) -> None:
    """What a model gets from Read is numbered, and quoting it back verbatim is honest.

    A live stage spent fifteen minutes refused for this, re-read the file, and produced the
    same quote again. The verdict it got was the one reserved for fabrication.
    """
    (tmp_path / "m.py").write_text("def add(a, b):\n    return a + b\n")
    numbered = "1 def add(a, b):\n2     return a + b"
    verdict = vf.file_quote(str(tmp_path), "m.py", 1, 2, numbered)
    assert verdict.ok, verdict


def test_numbers_that_do_not_count_are_left_as_content(tmp_path) -> None:
    """A gutter counts. Two lines that happen to start with a digit are code."""
    (tmp_path / "m.py").write_text("10 apples\n40 pears\n")
    verdict = vf.file_quote(str(tmp_path), "m.py", 1, 2, "10 apples\n40 pears")
    assert verdict.ok, verdict


def test_stripping_a_gutter_cannot_rescue_a_wrong_quote(tmp_path) -> None:
    (tmp_path / "m.py").write_text("def add(a, b):\n    return a + b\n")
    wrong = "1 def add(a, b):\n2     return a - b"
    verdict = vf.file_quote(str(tmp_path), "m.py", 1, 2, wrong)
    assert not verdict.ok, verdict

def test_two_ranges_quoted_with_an_elision_are_checked_separately(tmp_path) -> None:
    """A definition and its use, cited together -- the citation a review wants to make.

    Attaching the whole block to the last range failed both halves of a correct claim: quote
    not present on one, incomplete on the other.
    """
    (tmp_path / "m.py").write_text(
        "LOCK = 1\n" + "filler\n" * 3 + "def use():\n    return LOCK\n")
    text = ("CLAIM: the lock is global.\n"
            "EVIDENCE: m.py:1, 5-6\n"
            "QUOTE:\nLOCK = 1\n...\ndef use():\n    return LOCK\n")
    claims, _ = vf.parse_ledger(text)
    quoted = [(e["start"], e["end"], e["quote"]) for e in claims[0]["evidence"]]
    assert quoted[0][2] == "LOCK = 1", quoted
    assert quoted[1][0] == 5, quoted
    for citation in claims[0]["evidence"]:
        verdict = vf.file_quote(str(tmp_path), citation["path"], citation["start"],
                                citation["end"], citation["quote"])
        assert verdict.ok, (citation, verdict)


def test_an_elision_inside_a_single_range_is_still_content(tmp_path) -> None:
    """One range, one quote: splitting it would invent fragments nobody cited."""
    (tmp_path / "m.py").write_text("a = 1\n...\nb = 2\n")
    text = ("CLAIM: it elides.\n"
            "EVIDENCE: m.py:1-3\n"
            "QUOTE:\na = 1\n...\nb = 2\n")
    claims, _ = vf.parse_ledger(text)
    citation = claims[0]["evidence"][0]
    verdict = vf.file_quote(str(tmp_path), "m.py", 1, 3, citation["quote"])
    assert verdict.ok, verdict


def test_a_single_line_quote_with_a_gutter_the_citation_vouches_for(tmp_path) -> None:
    """One number is not a sequence, so the citation is what vouches for it.

    Single-line citations are the commonest kind. A live plan stage was refused three times for
    fabrication over quotes that were correct to the byte once the tab-separated gutter came off.
    """
    (tmp_path / "m.py").write_text("a = 1\nb = 2\nc = 3\n")
    verdict = vf.file_quote(str(tmp_path), "m.py", 2, 2, "2\tb = 2")
    assert verdict.ok, verdict


def test_a_gutter_that_disagrees_with_the_citation_is_not_stripped(tmp_path) -> None:
    (tmp_path / "m.py").write_text("a = 1\nb = 2\nc = 3\n")
    verdict = vf.file_quote(str(tmp_path), "m.py", 3, 3, "2\tb = 2")
    assert not verdict.ok, verdict


def test_a_single_line_that_merely_starts_with_a_number_is_content(tmp_path) -> None:
    (tmp_path / "m.py").write_text("40 pears\n")
    verdict = vf.file_quote(str(tmp_path), "m.py", 1, 1, "40 pears")
    assert verdict.ok, verdict


def test_commentary_after_the_closing_fence_is_not_part_of_the_quote(tmp_path) -> None:
    """A quote runs to the next header, so a model that quotes and then explains puts its
    explanation inside the quote.

    Measured: a plan stage was refused for fabrication over a quote that matched the file to the
    byte, with two sentences of commentary appended to it.
    """
    (tmp_path / "m.py").write_text("def add(a, b):\n    return a + b\n")
    quoted = ("```python\ndef add(a, b):\n    return a + b\n```\n\n"
              "This is where the addition happens, and it is why the total is wrong.")
    verdict = vf.file_quote(str(tmp_path), "m.py", 1, 2, quoted)
    assert verdict.ok, verdict


def test_an_unclosed_fence_still_gives_up_its_content(tmp_path) -> None:
    (tmp_path / "m.py").write_text("def add(a, b):\n    return a + b\n")
    verdict = vf.file_quote(str(tmp_path), "m.py", 1, 2, "```\ndef add(a, b):\n    return a + b")
    assert verdict.ok, verdict


def test_a_citation_that_names_lines_but_no_file_is_resolved() -> None:
    """A stage wrote sixteen claims, each carrying real quoted source and a line number, and every
    one was refused for citing nothing: it had written "line 212 checks" rather than naming the
    file. Asking the tree which file holds that text at that line is stricter than believing a
    path the model typed."""
    body = Path(ROOT, "scripts/cc_verify.py").read_text().split("\n")
    n = next(i for i, l in enumerate(body, 1) if l.startswith("BARE_LINES = re.compile"))
    text = "CLAIM: the bare-line pattern exists.\nEVIDENCE: line %d defines it.\nQUOTE:\n%s\n" % (
        n, body[n - 1])
    claims, _ = vf.parse_ledger(text, ROOT)
    assert claims[0]["path"] == "scripts/cc_verify.py", claims[0]
    assert vf.verify_ledger(ROOT, text)[0][1].ok, vf.verify_ledger(ROOT, text)[0][1].detail


def test_a_quote_that_matches_two_files_stays_uncited() -> None:
    """Resolution is only honest while it is unambiguous: one line of `import os` is in every
    file, and guessing which would accept a claim nobody can check."""
    assert vf.resolve_path(ROOT, 1, 1, "") is None


def test_the_written_out_line_label_is_not_a_fabrication() -> None:
    """`QUOTE: Line 174: `code`` is the gutter written out in words. Compared literally it fails
    as "quote not present", the verdict reserved for invention."""
    body = Path(ROOT, "scripts/cc_verify.py").read_text().split("\n")
    n = next(i for i, l in enumerate(body, 1) if l.startswith("BARE_LINES = re.compile"))
    quoted = "Line %d: `%s`" % (n, body[n - 1])
    assert vf.file_quote(ROOT, "scripts/cc_verify.py", n, n, quoted).ok


def test_a_path_followed_by_its_lines_is_a_citation() -> None:
    """Every stage that has run here writes `guard.py lines 174, 188-189` rather than
    `guard.py:174`. Refusing that is a quarrel about punctuation, not about evidence."""
    found = vf._classify_all("`scripts/cc_verify.py` lines 174, 188-189")
    assert [(f["path"], f["start"], f["end"]) for f in found] == [
        ("scripts/cc_verify.py", 174, 174), ("scripts/cc_verify.py", 188, 189)], found


def test_a_snippet_labelled_with_its_line_is_not_a_fabrication() -> None:
    """`` `code` (line 174) `` is the gutter again, written after the code instead of before it."""
    body = Path(ROOT, "scripts/cc_verify.py").read_text().split("\n")
    n = next(i for i, l in enumerate(body, 1) if l.startswith("PATH_LINES = re.compile"))
    assert vf.file_quote(ROOT, "scripts/cc_verify.py", n, n, "`%s` (line %d)" % (body[n - 1], n)).ok


def test_a_quote_wrapped_in_backticks_is_not_a_fabrication() -> None:
    """A stage quoted a line perfectly and was told it was not present in the file. The two
    differed by one backtick at each end."""
    body = Path(ROOT, "scripts/cc_verify.py").read_text().split("\n")
    n = next(i for i, l in enumerate(body, 1) if l.startswith("PATH_LINES = re.compile"))
    assert vf.file_quote(ROOT, "scripts/cc_verify.py", n, n, "`%s`" % body[n - 1]).ok


def test_a_quote_rewrapped_onto_one_line_is_not_a_fabrication() -> None:
    """Two lines of a wrapped call, quoted as the one line they read as. The model read the right
    place and tidied it, which is not inventing it."""
    body = Path(ROOT, "scripts/cc_verify.py").read_text().split("\n")
    n = next(i for i, l in enumerate(body, 1) if l.startswith("def _flat("))
    joined = " ".join(l.strip() for l in body[n - 1:n + 1])
    verdict = vf.file_quote(ROOT, "scripts/cc_verify.py", n, n + 1, joined)
    assert verdict.ok, verdict.detail
    assert verdict.kind == vf.REWRAPPED, verdict.detail


def test_rewrapping_does_not_excuse_the_wrong_text() -> None:
    assert not vf.file_quote(ROOT, "scripts/cc_verify.py", 1, 2, "def nothing_of_the_sort():").ok


def test_a_header_that_says_the_range_in_passing_is_still_a_header() -> None:
    """`QUOTE (lines 212-217):` went unrecognised as a header, so the quote attached to nothing
    and nine otherwise complete claims were reported as incomplete file_quotes."""
    claims, _ = vf.parse_ledger(
        "CLAIM: x\nEVIDENCE: a/b.py:10-11\nQUOTE (lines 10-11):\n    pass\n")
    assert claims[0]["quote"] == "    pass", claims[0]


def test_lines_joined_with_slashes_are_still_lines() -> None:
    """A stage quoted six lines of the guard on one line with ` / ` between them."""
    body = Path(ROOT, "scripts/cc_verify.py").read_text().split("\n")
    n = next(i for i, l in enumerate(body, 1) if l.startswith("def _flat("))
    joined = " / ".join("`%s`" % l.strip() for l in body[n - 1:n + 1])
    assert vf.file_quote(ROOT, "scripts/cc_verify.py", n, n + 1, joined).ok


def test_a_slash_in_code_is_not_a_line_break() -> None:
    assert vf._unslashed("a = b / c") == "a = b / c"


def test_a_citation_written_lines_first_is_still_a_citation() -> None:
    """`line 212-217 of guard.py` is how a claim reads when the sentence begins with where."""
    found = vf._classify_all("line 212-217 of scripts/cc_verify.py")
    assert [(f["path"], f["start"], f["end"])
            for f in found] == [("scripts/cc_verify.py", 212, 217)], found


def test_an_unknown_declared_as_a_claim_is_an_unknown() -> None:
    """Two came back as `CLAIM: UNKNOWN: I could not verify ...` and were refused for citing
    nothing -- for saying the one thing the stance calls a complete answer, under the wrong
    header."""
    claims, unknowns = vf.parse_ledger("CLAIM: UNKNOWN: whether the suite is slow\n")
    assert claims == [], claims
    assert unknowns == ["whether the suite is slow"], unknowns


def test_a_quote_given_on_the_citation_line_is_read() -> None:
    """`guard.py, line 208: "# comment"` -- the quote where the citation is, rather than under a
    QUOTE header. Eight claims in a row were reported as citing nothing for writing it this way."""
    body = Path(ROOT, "scripts/cc_verify.py").read_text().split("\n")
    n = next(i for i, l in enumerate(body, 1) if l.startswith("def _inline_quote("))
    text = 'CLAIM: x\nEVIDENCE: scripts/cc_verify.py, line %d: "%s"\n' % (n, body[n - 1])
    claims, _ = vf.parse_ledger(text, ROOT)
    assert claims[0]["quote"] == body[n - 1], claims[0]
    assert vf.verify_ledger(ROOT, text)[0][1].ok, vf.verify_ledger(ROOT, text)[0][1].detail


def test_prose_after_a_citation_is_not_taken_for_a_quote() -> None:
    """Comparing a claim's own words against the file would report fabrication where there was
    only a missing quote."""
    found = vf._classify_all("scripts/cc_verify.py line 10 defines the pattern it needs")
    assert found and "quote" not in found[0], found


def test_every_citation_in_a_multi_line_evidence_block_is_read() -> None:
    """Five citations arrived under one EVIDENCE header, on lines of their own. Only the first was
    read and the claim was reported as citing nothing."""
    claims, _ = vf.parse_ledger(
        "CLAIM: x\nEVIDENCE: a/b.py line 10\nc/d.py line 20\ne/f.py line 30\n")
    paths = [e.get("path") for e in claims[0]["evidence"]]
    assert paths == ["a/b.py", "c/d.py", "e/f.py"], claims[0]["evidence"]


def test_a_bold_header_is_read_as_a_header() -> None:
    """A claims stage wrote every header as `**CLAIM: ...**` with the evidence in the same sentence,
    and the parser scored the answer nought claims -- earning it a refusal that said no claims were
    stated, which was the one thing it had not done wrong."""
    answer = ("**CLAIM: The boundary is strict at 30 seconds.** `sleep 30` returned \"allow\".\n"
              "**UNKNOWN: Whether --max-sleep is exposed by the driver.** I did not look.\n")
    claims, unknowns = vf.parse_ledger(answer)
    assert [c["claim"] for c in claims] == ["The boundary is strict at 30 seconds."], claims
    assert len(unknowns) == 1, unknowns


def test_a_citation_after_a_bold_header_is_evidence() -> None:
    answer = "**CLAIM: the rule matches text, not sleeps.** See scripts/cc-context-guard.py:174-176\n"
    claims, _ = vf.parse_ledger(answer)
    assert claims and claims[0]["evidence"], claims
    assert claims[0]["evidence"][0]["path"] == "scripts/cc-context-guard.py", claims[0]["evidence"]


def test_prose_after_a_bold_header_is_not_turned_into_evidence() -> None:
    """Inventing a citation for a claim that has none is worse than reporting that it has none."""
    answer = "**CLAIM: the rule is broader than its intent.** I read it and it looked wrong to me.\n"
    claims, _ = vf.parse_ledger(answer)
    assert claims and not claims[0]["evidence"], claims


def test_a_plain_header_still_parses() -> None:
    answer = ("CLAIM: the guard denies a long sleep\n"
              "EVIDENCE: scripts/cc-context-guard.py:174\n"
              "QUOTE: _SLEEP = re.compile\n")
    claims, _ = vf.parse_ledger(answer)
    assert len(claims) == 1 and claims[0]["evidence"], claims


def test_a_finding_is_a_claim() -> None:
    """A seven-finding report with a path and a line range under every heading parsed as nought
    claims, because the model called them findings and the parser only knew the word CLAIM."""
    answer = ("**Finding 1: the rule matches text, not sleeps.**\n"
              "\n"
              "`scripts/cc_verify.py`, lines 10-12: it does something.\n")
    claims, _ = vf.parse_ledger(answer)
    assert len(claims) == 1, claims
    assert claims[0]["evidence"] and claims[0]["evidence"][0]["start"] == 10, claims[0]["evidence"]


def test_an_inline_quote_stops_at_its_own_delimiter() -> None:
    """Greedy, this ran to the last backtick on the line and swallowed the prose between, so an
    incomplete citation was reported as a quote that is not present -- which reads as fabrication."""
    answer = ("CLAIM: the off-switch lifts every rule\n"
              "EVIDENCE: scripts/cc_verify.py lines 10-11: `first` where `second`. This means x.\n")
    claims, _ = vf.parse_ledger(answer)
    assert claims and not claims[0]["evidence"][0].get("quote"), claims[0]["evidence"]


def test_a_lone_backticked_span_is_the_quote() -> None:
    answer = "CLAIM: the default is 30\nEVIDENCE: scripts/cc_verify.py lines 10-10: `default=30`.\n"
    claims, _ = vf.parse_ledger(answer)
    assert claims[0]["evidence"][0].get("quote") == "default=30", claims[0]["evidence"]


def test_a_quote_that_is_not_in_the_file_still_fails_without_line_numbers() -> None:
    """Locating a quote by its text must not become a way to pass without one."""
    with tempfile.TemporaryDirectory() as root:
        pathlib.Path(root, "m.py").write_text("def f():\n    return 1\n")
        assert vf.locate(root, "m.py", "def f():\n    return 1") == (1, 2)
        assert vf.locate(root, "m.py", "return 99999") is None


def test_a_short_fragment_is_not_accepted_as_a_clipped_quote() -> None:
    """A whole line quoted whole is fine however short. A few words clipped out of the middle of a
    longer line are not: `allow()` occurs eleven times in the guard and would vouch for anything."""
    with tempfile.TemporaryDirectory() as root:
        pathlib.Path(root, "m.py").write_text("if off_switch.exists():\n    allow()\n")
        assert vf.locate(root, "m.py", "    allow()") == (2, 2)
        assert vf.locate(root, "m.py", "exists()") is None


def test_a_clipped_quote_must_sit_in_the_lines_it_cites() -> None:
    with tempfile.TemporaryDirectory() as root:
        pathlib.Path(root, "m.py").write_text(
            'deny("Do not sleep for %ds. Run it in the foreground and wait there."\n'
            "      % max(naps))\n")
        good = vf.file_quote(root, "m.py", 1, 2,
                             'Do not sleep for %ds. Run it in the foreground and wait there."\n'
                             "      % max(naps))")
        assert good.kind == vf.CLIPPED and good.ok, good
        bad = vf.file_quote(root, "m.py", 1, 2,
                            'Do not sleep for %ds. Run it in the background and poll it."\n'
                            "      % max(naps))")
        assert not bad.ok, bad


def test_a_quote_inside_the_cited_range_is_evidence_for_it() -> None:
    """A stage cited the rule and the function it calls, then quoted the lines its claim turned on.
    Judged as though the quote had to be the whole range, that came back as wrong-lines."""
    with tempfile.TemporaryDirectory() as root:
        pathlib.Path(root, "m.py").write_text(
            "\n".join(["import re", "", "_SLEEP = re.compile(r'sleep')", "",
                       "def naps_in(command):", "    return []", "", "x = 1"]) + "\n")
        good = vf.file_quote(root, "m.py", 1, 8, "def naps_in(command):\n    return []")
        assert good.kind == vf.PARTIAL and good.ok, good


def test_a_quote_outside_the_cited_range_is_still_wrong_lines() -> None:
    with tempfile.TemporaryDirectory() as root:
        pathlib.Path(root, "m.py").write_text(
            "\n".join(["import re", "", "def naps_in(command):", "    return []"]) + "\n")
        moved = vf.file_quote(root, "m.py", 1, 2, "def naps_in(command):\n    return []")
        assert moved.kind == vf.WRONG_LINES and not moved.ok, moved
        assert "at line 3" in moved.detail, moved.detail


def test_a_quote_given_as_a_python_string_with_prose_after_it_is_read() -> None:
    """Verbatim from a live claims stage: the quote arrives single-quoted with `\\n` escapes and the
    finding's point tacked on after the closing quote. Read literally it is one long line that
    matches nothing, and the stage was refused for a citation that was correct."""
    with tempfile.TemporaryDirectory() as root:
        pathlib.Path(root, "g.py").write_text(
            'x = 1\ndeny("Do not sleep for %ds. Run it in the foreground: "\n'
            '     "polling costs the whole wait."\n     % max(naps))\n')
        text = ('CLAIM: the message says nothing about the off-switch\n'
                'QUOTE: g.py:2-4 \'deny("Do not sleep for %ds. Run it in the foreground: "\\n'
                '     "polling costs the whole wait."\\n     % max(naps))\' -- no off-switch.\n')
        claims, _ = vf.parse_ledger(text, root)
        piece = claims[0]["evidence"][0]
        assert piece["path"] == "g.py" and (piece["start"], piece["end"]) == (2, 4)
        assert vf.file_quote(root, "g.py", 2, 4, piece["quote"]).ok


def test_prose_after_a_quote_cannot_smuggle_in_a_fabricated_one() -> None:
    """The prose is dropped, not merged: a quote that is not in the file still fails."""
    with tempfile.TemporaryDirectory() as root:
        pathlib.Path(root, "g.py").write_text("x = 1\ny = 2\n")
        text = "CLAIM: it deletes the database\nQUOTE: g.py:1-2 'drop_all_tables()' -- as shown.\n"
        claims, _ = vf.parse_ledger(text, root)
        assert not vf.file_quote(root, "g.py", 1, 2, claims[0]["evidence"][0]["quote"]).ok


def test_a_claim_that_names_the_file_supplies_it_to_the_quotes_under_it() -> None:
    """Verbatim shape from run 7: the path in the claim's own sentence, the line after the quote.
    Eleven citations of one file came back as "missing a file and a line range" -- every one of them
    said where, twice, in the two places a person would look."""
    with tempfile.TemporaryDirectory() as root:
        pathlib.Path(root, "g.py").write_text("import os\nOFF = Path('/tmp/off')\nSWITCHES = (OFF,)\n")
        text = ("CLAIM: The switches are defined at lines 2-3 of `g.py`.\n\n"
                "QUOTE: `OFF = Path('/tmp/off')` (line 2)\n"
                "QUOTE: `SWITCHES = (OFF,)` (line 3)\n")
        claims, _ = vf.parse_ledger(text, root)
        pieces = claims[0]["evidence"]
        assert len(pieces) == 2, pieces
        for piece in pieces:
            assert piece["path"] == "g.py"
            assert vf.file_quote(root, piece["path"], piece["start"], piece["end"],
                                 piece["quote"]).ok, piece


def test_an_inherited_path_does_not_excuse_the_wrong_line() -> None:
    with tempfile.TemporaryDirectory() as root:
        pathlib.Path(root, "g.py").write_text("import os\nOFF = Path('/tmp/off')\n")
        text = "CLAIM: about g.py.\n\nQUOTE: `OFF = Path('/tmp/off')` (line 9)\n"
        claims, _ = vf.parse_ledger(text, root)
        piece = claims[0]["evidence"][0]
        assert (piece["path"], piece["start"]) == ("g.py", 9)
        assert not vf.file_quote(root, "g.py", 9, 9, piece["quote"]).ok


def _call(command: str, printed: str):
    import cc_evidence
    return cc_evidence.ToolCall(agent="claims", tool="Bash", call_id="1",
                                args={"command": command}, text=printed)


def test_a_probe_described_as_the_opposite_of_what_it_printed_fails() -> None:
    """The looseness that lets "DENIED" stand for `"permissionDecision": "deny"` must not let it
    stand for a run that allowed."""
    ran = _call('echo \'{"tool_name": "Bash", "tool_input": {"command": "dd of=/tmp/x"}}\' | python3 g.py',
                "EXIT:0")
    good = vf.command_result([ran], 'echo \'{"tool_name": "Bash", "tool_input": {"command": "dd of=/tmp/x"}}\' | python3 g.py',
                             "ALLOWED (no deny output, exit 0)")
    assert good.ok, good
    bad = vf.command_result([ran], 'echo \'{"tool_name": "Bash", "tool_input": {"command": "dd of=/tmp/x"}}\' | python3 g.py',
                            "DENIED with permissionDecision deny")
    assert not bad.ok, bad


def test_a_command_nobody_ran_is_still_unverified() -> None:
    ran = _call('echo \'{"tool_input": {"command": "touch /tmp/x"}}\' | python3 g.py', "deny")
    verdict = vf.command_result([ran], 'echo \'{"tool_input": {"command": "shred /tmp/x"}}\' | python3 g.py',
                                "DENIED")
    assert verdict.kind == vf.UNVERIFIED, verdict


def test_one_case_of_a_loop_is_judged_on_its_own_output() -> None:
    """Five probes in one loop print five results. A claim about the one that was allowed must not
    be settled by the four that were denied."""
    loop = _call('for c in "cp /dev/null /tmp/sw" "dd of=/tmp/sw"; do echo "CMD: $c"; echo "$c" | python3 g.py; echo ---; done',
                 'CMD: cp /dev/null /tmp/sw\n{"permissionDecision": "deny"}\n---\n'
                 'CMD: dd of=/tmp/sw\nEXIT:0\n---\n')
    denied = vf.command_result([loop], '{"command": "cp /dev/null /tmp/sw"}', "DENIED")
    assert denied.ok, denied
    lied = vf.command_result([loop], '{"command": "dd of=/tmp/sw"}', "DENIED")
    assert not lied.ok, "the case that was allowed was judged on another case's output"
    honest = vf.command_result([loop], '{"command": "dd of=/tmp/sw"}', "ALLOWED, no deny printed")
    assert honest.ok, honest


def test_silence_claimed_of_a_command_that_said_something_fails() -> None:
    ran = _call("pytest -q", "3 failed, 40 passed")
    verdict = vf.command_result([ran], "pytest -q", "no failures, nothing printed")
    assert not verdict.ok, verdict


def test_three_quotes_on_one_header_are_three_citations() -> None:
    """A stage with three quotes for one claim wrote them on a single QUOTES line, each followed by
    its line number. Nine correct findings were reported as citing nothing."""
    with tempfile.TemporaryDirectory() as root:
        src = Path(root, "guard.py")
        src.write_text("import re\nVERBS = r\"(?:touch|mv)\"\nCALLS = r\"(?:open|Path)\"\n")
        text = ('CLAIM: the verbs and the calls are two separate patterns\n'
                'QUOTES: "VERBS = r"(?:touch|mv)"" (line 2); "CALLS = r"(?:open|Path)"" (line 3)\n')
        claims, _ = vf.parse_ledger(text, root=root)
        assert len(claims) == 1, claims
        pieces = claims[0]["evidence"]
        assert len(pieces) == 2, pieces
        for piece in pieces:
            verdict = vf.file_quote(root, "guard.py", piece["start"], piece["end"],
                                    piece["quote"])
            assert verdict.ok, (piece, verdict.kind)


def test_a_quote_on_a_shared_header_still_has_to_be_in_the_file() -> None:
    """The plural form is a spelling, not an exemption."""
    with tempfile.TemporaryDirectory() as root:
        Path(root, "guard.py").write_text("import re\nVERBS = r\"(?:touch|mv)\"\n")
        text = ('CLAIM: two patterns\n'
                'QUOTES: guard.py "VERBS = r"(?:touch|mv)"" (line 2); '
                'guard.py "CALLS = whatever I like" (line 2)\n')
        claims, _ = vf.parse_ledger(text, root=root)
        verdicts = [vf.file_quote(root, "guard.py", p["start"], p["end"], p["quote"])
                    for p in claims[0]["evidence"] if p.get("quote")]
        assert any(not v.ok for v in verdicts), [v.kind for v in verdicts]


def test_a_command_written_under_run_is_read_as_a_command() -> None:
    ran, _ = vf.parse_ledger('CLAIM: the hook allows it\n'
                             'RUN: echo x | python3 guard.py returns "allow"\n')
    piece = ran[0]["evidence"][0]
    assert piece["kind"] == "command_result", piece
    assert piece["command"] == "echo x | python3 guard.py", piece
    assert piece["expect"] == '"allow"', piece


def test_a_command_under_run_that_nobody_ran_is_not_evidence() -> None:
    """RUN is a shorter way to say what the contract asks for, not a way to skip the check."""
    claims, _ = vf.parse_ledger('CLAIM: it allows it\n'
                                'RUN: python3 guard.py returns "allow"\n')
    piece = claims[0]["evidence"][0]
    verdict = vf.command_result([], piece["command"], piece["expect"])
    assert not verdict.ok, verdict.kind


def test_a_quote_that_unescaped_a_double_quote_is_still_the_line() -> None:
    """A stage writing JSON quotes gave the file's [^\\s'\\";|&] as [^\\s'";|&]. Eleven citations of
    one regex could not be attributed to any file over that backslash."""
    with tempfile.TemporaryDirectory() as root:
        Path(root, "g.py").write_text('import re\nname = r"(?:[^\\s\'\\";|&]*/)?" + rest\n')
        verdict = vf.file_quote(root, "g.py", 2, 2, 'name = r"(?:[^\\s\'";|&]*/)?" + rest')
        assert verdict.ok, verdict
        assert verdict.kind == vf.ESCAPED, verdict


def test_unescaping_does_not_make_a_different_line_match() -> None:
    with tempfile.TemporaryDirectory() as root:
        Path(root, "g.py").write_text('import re\nname = r"(?:[^\\s\'\\";|&]*/)?" + rest\n')
        assert not vf.file_quote(root, "g.py", 2, 2, 'name = r"(?:[^x]*/)?" + rest').ok


def test_a_near_miss_is_given_the_address_it_will_be_refused_at() -> None:
    """A stage quoted two lines, dedenting the first and keeping the second, so the relative indent
    changed and the quote is rightly refused. Unresolvable, it was refused as citing nothing -- which
    sends it looking for a citation it had already written."""
    with tempfile.TemporaryDirectory() as root:
        Path(root, "g.py").write_text("def f(x):\n    if x:\n        return False\n")
        _tracked_repo(root)
        quote = "if x:\n        return False"
        assert vf.file_quote(root, "g.py", 2, 3, quote).kind == vf.RETOUCHED
        assert vf.resolve_path(root, 2, 3, quote) == "g.py"


def test_the_address_is_still_not_an_approval() -> None:
    with tempfile.TemporaryDirectory() as root:
        Path(root, "g.py").write_text("def f(x):\n    if x:\n        return False\n")
        _tracked_repo(root)
        assert vf.resolve_path(root, 2, 3, "if y:\n        return True") is None


def _tracked_repo(root: str) -> None:
    """resolve_path asks git what the tree holds, so a bare directory answers nothing."""
    for command in (["git", "init", "-q"], ["git", "add", "-A"]):
        subprocess.run(command, cwd=root, check=True, capture_output=True)


def test_a_header_bolded_through_its_colon_keeps_its_sentence() -> None:
    """`**CLAIM 1:** text` is how anyone writing markdown bolds a heading. Split at the closing mark
    the claim was empty and the sentence was trailing prose, so eight findings arrived as
    `claim 1 ()` and were refused for citing nothing."""
    text = ("**CLAIM 1:** tampers() denies creation but not removal.\n"
            "**EVIDENCE:** scripts/g.py:247-249\n")
    claims, _ = vf.parse_ledger(text)
    assert len(claims) == 1, claims
    assert claims[0]["claim"].startswith("tampers() denies creation"), claims[0]
    assert claims[0]["evidence"][0]["start"] == 247, claims[0]["evidence"]


def test_a_header_bolded_after_its_sentence_still_splits_there() -> None:
    text = "**CLAIM: the rule is broader than its intent.** scripts/g.py:12-14\n"
    claims, _ = vf.parse_ledger(text)
    assert claims[0]["claim"] == "the rule is broader than its intent.", claims
    assert claims[0]["evidence"][0]["start"] == 12, claims[0]["evidence"]


def test_a_conclusion_under_the_evidence_is_not_a_second_claim() -> None:
    """A report of four findings scored eight claims, half citing nothing, because each FINDING line
    -- the conclusion drawn from the evidence above it -- was read as a claim of its own."""
    text = ("**CLAIM 1:** the rule denies creation.\n"
            "**EVIDENCE:** scripts/g.py:247-249\n"
            "**FINDING:** creation is denied and removal is not.\n")
    claims, _ = vf.parse_ledger(text)
    assert len(claims) == 1, [c["claim"] for c in claims]


def test_a_numbered_finding_is_still_a_heading() -> None:
    text = ("**Finding 1:** the rule denies creation.\n"
            "**EVIDENCE:** scripts/g.py:247-249\n")
    claims, _ = vf.parse_ledger(text)
    assert len(claims) == 1, claims
    assert claims[0]["claim"].startswith("the rule denies creation"), claims[0]


def test_probes_reported_in_prose_are_read_as_the_commands_they_were() -> None:
    """Eight findings established by running the hook cited nothing, because the stage reported each
    run in a sentence instead of under the header the contract names."""
    text = ("**CLAIM 1:** creation is denied and removal is not.\n"
            "**EVIDENCE:** Ran two hooks. `touch /tmp/cc-guard-off` -- denied. "
            "`rm /tmp/cc-guard-off` -- allowed.\n")
    claims, _ = vf.parse_ledger(text)
    pieces = [p for p in claims[0]["evidence"] if p.get("kind") == "command_result"]
    assert len(pieces) == 2, claims[0]["evidence"]
    assert pieces[0]["command"] == "touch /tmp/cc-guard-off", pieces
    assert pieces[0]["expect"] == "denied", pieces


def test_a_probe_reported_in_prose_still_has_to_have_been_run() -> None:
    piece = [p for p in vf.parse_ledger(
        "**CLAIM 1:** it is denied.\n"
        "**EVIDENCE:** `touch /tmp/cc-guard-off` -- denied.\n")[0][0]["evidence"]
        if p.get("kind") == "command_result"][0]
    assert not vf.command_result([], piece["command"], piece["expect"]).ok


def test_backticked_code_in_prose_is_not_mistaken_for_a_probe() -> None:
    claims, _ = vf.parse_ledger("**CLAIM 1:** the rule is narrow.\n"
                                "**EVIDENCE:** `tampers()` returns False here.\n")
    assert not [p for p in claims[0]["evidence"] if p.get("kind") == "command_result"], claims[0]


def test_prose_between_two_snippets_is_not_a_command() -> None:
    """Backticks alternate, so a scan that pairs them wrongly reads the words between two snippets as
    something the stage ran: `OFF_SWITCH`. Only checks `DEPTH_OFF` produced ". Only checks"."""
    claims, _ = vf.parse_ledger(
        "**CLAIM 1:** the check is narrow.\n"
        "**EVIDENCE:** `OFF_SWITCH`. Only checks `DEPTH_OFF`. Does not mention the other.\n")
    phantoms = [p for p in claims[0]["evidence"] if p.get("kind") == "command_result"]
    assert not phantoms, phantoms


def test_the_line_a_denial_comes_from_is_not_part_of_what_it_printed() -> None:
    claims, _ = vf.parse_ledger("**CLAIM 1:** creation is denied.\n"
                                "**EVIDENCE:** `touch /tmp/cc-guard-off` -- denied (line 307-310).\n")
    piece = [p for p in claims[0]["evidence"] if p.get("kind") == "command_result"][0]
    assert piece["expect"] == "denied", piece


def test_a_label_in_front_of_the_code_is_read_as_the_citation() -> None:
    """`QUOTE: Line 246: `code`` -- the label before the line rather than after it. It was stripped
    for the comparison and never read as the citation, so a ledger of nine quoted lines, each
    carrying its own number, was refused nine times for missing a file and a line range."""
    with tempfile.TemporaryDirectory() as root:
        Path(root, "g.py").write_text("import re\nVERBS = 1\nCALLS = 2\n")
        _tracked_repo(root)
        claims, _ = vf.parse_ledger("CLAIM: two names\nQUOTE: Line 2: `VERBS = 1`\n", root=root)
        piece = claims[0]["evidence"][0]
        assert piece["start"] == 2, piece
        assert piece["path"] == "g.py", piece
        assert vf.file_quote(root, piece["path"], piece["start"], piece["end"], piece["quote"]).ok


def test_a_labelled_quote_still_has_to_be_at_that_line() -> None:
    with tempfile.TemporaryDirectory() as root:
        Path(root, "g.py").write_text("import re\nVERBS = 1\nCALLS = 2\n")
        _tracked_repo(root)
        claims, _ = vf.parse_ledger("CLAIM: two names\nQUOTE: Line 3: `VERBS = 1`\n", root=root)
        piece = claims[0]["evidence"][0]
        assert not vf.file_quote(root, "g.py", piece["start"], piece["end"], piece["quote"]).ok


def test_a_command_keeps_none_of_the_backticks_it_arrived_in() -> None:
    piece = vf.parse_ledger("CLAIM: it is denied\n"
                            "EVIDENCE: command: `echo hi | guard.py` -> deny\n")[0][0]["evidence"][0]
    assert piece["command"] == "echo hi | guard.py", piece


def test_a_quote_off_by_a_line_is_told_which_line_it_is_at() -> None:
    """Cited one line out, a quote resolved to no file at all, so the stage was told its citation was
    missing a file and a line range -- of a citation that named both."""
    with tempfile.TemporaryDirectory() as root:
        Path(root, "g.py").write_text("import re\nVERBS = 1\nCALLS = 2\nMORE = 3\n")
        _tracked_repo(root)
        assert vf.resolve_path(root, 3, 3, "VERBS = 1") == "g.py"
        assert vf.file_quote(root, "g.py", 3, 3, "VERBS = 1").kind == vf.WRONG_LINES


def test_a_quote_in_no_file_resolves_to_no_file() -> None:
    with tempfile.TemporaryDirectory() as root:
        Path(root, "g.py").write_text("import re\nVERBS = 1\n")
        _tracked_repo(root)
        assert vf.resolve_path(root, 2, 2, "NOTHING_LIKE_THIS = 9") is None
