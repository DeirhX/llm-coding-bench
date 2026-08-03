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
