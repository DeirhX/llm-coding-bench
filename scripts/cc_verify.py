#!/usr/bin/env python3
"""Check a claim's citations against reality, so a confident answer can be refused on arithmetic.

The gate cannot judge whether an answer is *good*. It can judge whether the evidence offered for it
survives being looked up, which is the failure this pipeline exists to catch: the model reaches a
plausible conclusion and stops, and nothing downstream distinguishes that from a checked one.

``file_quote`` is the verdict-heavy one, and its comparison rule comes from measurement rather than
taste. In the depth-gate spike the 31B produced five citations: all five correct in content and line
range, only three byte-exact. One was re-indented by four spaces; one silently tidied a genuinely
odd 13-space line in the source to 12. Byte equality would have failed 40 % of *correct* citations,
and a check that cries wolf gets switched off. So content is compared with indentation normalised,
and indentation drift is reported as its own non-fatal verdict -- worth surfacing, because that same
drift is what breaks this model's edits (see LOCAL_AGENT_OPS.md section 7).

The other three verifiers exist because not every claim is about a file:
  * ``command_result`` -- the claim rests on something a command printed, so the command must
    actually appear in the session's evidence with that text in its output;
  * ``log_match`` -- a pattern must be present in a named log;
  * ``absence`` -- the claim is that something does *not* exist, which is the one kind of claim a
    quote cannot support, and the one the model is most tempted to assert.

Every verifier fails closed: an unreadable file, an unparseable citation or a missing evidence
record is UNVERIFIED, never PASS. That is the opposite of the hook convention in
``cc-context-guard.py``, and deliberately so -- a guard that crashes should let work through, while
a verifier that cannot check something must not bless it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from typing import Iterable

PASS = "pass"
INDENT_DRIFT = "indent-drift"
RETOUCHED = "retouched"
REWRAPPED = "rewrapped"
CLIPPED = "clipped"
PARTIAL = "partial"
ESCAPED = "escaped"
WRONG_LINES = "wrong-lines"
FAIL = "fail"
UNVERIFIED = "unverified"

# Verdicts a claim may keep. INDENT_DRIFT is accepted because the content was right and the model
# shifted the whole block; it is reported so the drift is visible rather than silently normalised.
#
# RETOUCHED is not accepted and is not FAIL either. In the spike the model reproduced a block from
# task_timeout.py correctly except for one line, where the file has an odd 13-space indent and the
# quote has 12: it tidied the source while copying it. That is not fabrication and calling it
# "quote not present" would send the gate hunting for an invented citation. It is still an edited
# quote, and per-line whitespace changes are the exact defect behind this model's failed edits, so
# it has to be re-read rather than waved through.
# REWRAPPED is accepted for the same reason as INDENT_DRIFT and not for the reason RETOUCHED is
# refused. A wrapped call quoted as the one line it reads as has every token of the cited range in
# order; nothing has been tidied away, only the wrapping the file happened to use. RETOUCHED is
# refused because one line's indent differs from its neighbours', which in Python can change what
# the code means -- here the whole range is compared as a single run of text, so there is no line
# whose indent could be quietly wrong.
# CLIPPED is accepted because the text is in the file and the reader can find it. It is what
# quoting a wrapped string literal looks like: a stage quoting the deny message wrote `Do not sleep
# for %ds. Run the command in the foreground ...` for a line that begins `deny("Do not sleep for`,
# and three claims that had read the right lines were failed with "quote not present in", the
# verdict kept for fabrication. Each quoted line must still sit inside the file's line at the same
# place in the run, so a fragment cannot drift to somewhere else in the file.
# PARTIAL is accepted because the quote is inside the range the claim named. A stage cited
# `cc-context-guard.py:174-207` -- the rule and the function it calls -- and quoted the handful of
# lines its claim turned on, which is the sane way to cite a block. Judged as though the quote had
# to be the whole range, it came back as "wrong-lines, text is near line 144", a number in
# blank-stripped coordinates that names nothing in the file.
# ESCAPED is accepted because the difference is in the quoting, not in the code. A stage gave its
# quotes as JSON strings -- the only unambiguous way to write a regex containing newlines -- and
# wrote `(\w+)\'?` for the file's `(\w+)'?`, one backslash it added while escaping. Six correct
# citations out of eighteen were failed as "quote not present in", over that character.
ACCEPTABLE = {PASS, INDENT_DRIFT, REWRAPPED, CLIPPED, PARTIAL, ESCAPED}


@dataclass
class Verdict:
    kind: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.kind in ACCEPTABLE

    def __str__(self) -> str:
        return "%-13s %s" % (self.kind, self.detail)


def _significant(text: str, first: int = 0) -> list[str]:
    """Lines that carry content, with trailing whitespace, blank lines and code fences discarded.

    `first` is the line number the quote is claimed to start at, which is what makes a gutter on a
    single-line quote recognisable: one number proves nothing on its own, but one number that is
    the line the citation names is not a coincidence.
    """
    return [line.rstrip()
            for line in _unticked(_unslashed(
                _untrailed(_unlabelled(_ungutted(_unfenced(text), first), first), first)))
            .strip("\n").split("\n") if line.strip()]


# One separator only: a bar-style gutter puts the code straight after the bar, and a space-style one
# uses a single space. Eating a second space silently reindents the quote by one column, which the
# verifier then reports as whitespace drift -- a better verdict than fabrication, but still wrong.
_GUTTER = re.compile(r"^\s{0,8}(?P<n>\d{1,6})(?:\s*[|:>]|\s)(?P<code>.*)$")


def _ungutted(text: str, first: int = 0) -> str:
    """Drop a line-number gutter the model pasted along with the code.

    Reading a file gives the model `1013 def f(` and quoting it back verbatim is the honest thing to
    do, so it does -- and the comparison then fails with "quote not present in file", the verdict
    reserved for fabrication. One live stage spent fifteen minutes being refused for this and then
    re-reading the same file to produce the same quote.

    Only stripped when every content line carries a number and those numbers run consecutively: a
    gutter is a gutter because it counts. That leaves code that merely begins with a digit alone,
    and cannot turn a wrong quote into a right one, since what remains is still compared against the
    file byte for byte.
    """
    lines = [l for l in text.split("\n")]
    content = [l for l in lines if l.strip()]
    if not content:
        return text
    if len(content) == 1:
        # One number is not a sequence, so the only thing that can vouch for it is the citation:
        # a single-line quote whose gutter reads 1520, cited at line 1520. Single-line citations
        # are the commonest kind, and without this every one of them carrying a gutter is reported
        # as a fabrication.
        found = _GUTTER.match(content[0])
        if not found or not first or int(found.group("n")) != first:
            return text
        return "\n".join(found.group("code") if l is content[0] else l for l in lines)
    numbers, stripped = [], {}
    for line in content:
        found = _GUTTER.match(line)
        if not found or not found.group("code").strip():
            return text
        numbers.append(int(found.group("n")))
        stripped[line] = found.group("code")
    if any(b - a != 1 for a, b in zip(numbers, numbers[1:])):
        return text
    return "\n".join(stripped.get(l, l) for l in lines)


# `Line 174: `code`` -- the gutter written out in words, with the code in backticks after it. It is
# what the model produces when it quotes from a file it read with line numbers showing and wants to
# be helpful about where the line came from, and it is indistinguishable from a fabricated quote to
# a comparison that does not know the label is a label.
_LABEL = re.compile(r"^\s*[Ll]ines?\s+(?P<n>\d{1,6})(?:\s*[-\u2013]\s*\d{1,6})?\s*:\s*"
                    r"(?P<code>.*)$")


def _unlabelled(text: str, first: int = 0) -> str:
    """Drop a written-out line label the model put in front of each quoted line.

    Held to the same rule as the numeric gutter: a label is only a label if the citation vouches
    for it, either by naming the line it claims or by counting up from there. Otherwise a line of
    prose that happens to begin "Line 3:" would be silently rewritten.
    """
    lines = text.split("\n")
    content = [l for l in lines if l.strip()]
    if not content:
        return text
    numbers, stripped = [], {}
    for line in content:
        found = _LABEL.match(line)
        if not found or not found.group("code").strip():
            return text
        numbers.append(int(found.group("n")))
        stripped[line] = found.group("code").strip("`")
    if first and numbers[0] != first:
        return text
    if any(b - a != 1 for a, b in zip(numbers, numbers[1:])):
        return text
    return "\n".join(stripped.get(l, l) for l in lines)


# `` `code` (line 174) `` -- the snippet in backticks with the line it came from after it. The
# same impulse as the written-out gutter, from the other end of the line.
_TRAILING_LINE = re.compile(r"^(?P<code>.*?)\s*\((?:[Ll]ines?\s+)(?P<n>\d{1,6})"
                            r"(?:\s*[-\u2013]\s*\d{1,6})?\)\s*$")


def _untrailed(text: str, first: int = 0) -> str:
    """Drop a trailing `(line N)` label from each quoted line, on the citation's word."""
    lines = text.split("\n")
    content = [l for l in lines if l.strip()]
    if not content:
        return text
    numbers, stripped = [], {}
    for line in content:
        found = _TRAILING_LINE.match(line)
        if not found or not found.group("code").strip():
            return text
        numbers.append(int(found.group("n")))
        stripped[line] = found.group("code").strip().strip("`")
    if first and numbers[0] != first:
        return text
    return "\n".join(stripped.get(l, l) for l in lines)


# `` `first line` / `second line` `` -- several lines of a file quoted on one, with a slash between
# them. Only split when every piece is backticked, because a slash between two pieces of bare code
# is division far more often than it is a line break.
_SLASHED = re.compile(r"\s+/\s+")


def _unslashed(text: str) -> str:
    """Split a one-line quote back into the lines it was joined from."""
    if "\n" in text.strip():
        return text
    pieces = [p.strip() for p in _SLASHED.split(text.strip()) if p.strip()]
    if len(pieces) < 2 or not all(p.startswith("`") and p.endswith("`") and len(p) > 1
                                  for p in pieces):
        return text
    return "\n".join(pieces)


def _unticked(text: str) -> str:
    """Drop the inline backticks a quote is wrapped in, line by line.

    The same habit as the triple fence and no more meaningful: a stage quoted line 174 of the guard
    perfectly and had it refused as not present in the file, the two differing by one backtick at
    each end. Only a pair wrapping a whole line goes; a backtick inside the code stays, since there
    it is content.
    """
    lines = text.split("\n")
    content = [l for l in lines if l.strip()]
    if not content or not all(l.strip().startswith("`") and l.strip().endswith("`")
                              and len(l.strip()) > 1 for l in content):
        return text
    return "\n".join(l.strip().strip("`") if l.strip() else l for l in lines)


def _unfenced(text: str) -> str:
    """Drop a markdown code fence wrapped around a quote.

    Measured, not anticipated: in the false-premise arm the 31B wrapped a byte-perfect quote in
    ```python ... ```, and the fence lines made the comparison fail with "quote not present" -- the
    verifier's fabrication verdict, on a citation that was entirely correct. A gate that reports a
    fabrication whenever the model reaches for markdown is a gate that gets switched off. Only a
    fence enclosing the whole quote is stripped; an interior one stays, since it is then content.
    """
    lines = text.strip("\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) >= 2 and lines[0].lstrip().startswith("```"):
        # A quote runs to the next header, and a model that quotes then explains puts its
        # explanation inside the quote. The closing fence is a plainer boundary than the header is:
        # a live plan stage was refused for fabrication over a quote that matched the file to the
        # byte, with two sentences of commentary appended to it.
        for n, line in enumerate(lines[1:], start=1):
            if line.strip() == "```":
                return "\n".join(lines[1:n])
        lines = lines[1:]
    return "\n".join(lines)


def _dedented(lines: Iterable[str]) -> list[str]:
    return textwrap.dedent("\n".join(lines)).split("\n")


def file_quote(root: str, path: str, start: int, end: int, quote: str) -> Verdict:
    """Do lines [start, end] of `path` contain `quote`, allowing for reindentation?"""
    full = os.path.join(root, path)
    if not os.path.isfile(full):
        return Verdict(UNVERIFIED, "no such file: %s" % path)
    try:
        lines = open(full, encoding="utf-8", errors="replace").read().split("\n")
    except OSError as exc:
        return Verdict(UNVERIFIED, "unreadable: %s" % exc)
    if start < 1 or end < start or start > len(lines):
        return Verdict(FAIL, "line range %d-%d outside %s (%d lines)" % (start, end, path, len(lines)))

    cited = _significant("\n".join(lines[start - 1:end]))
    quoted = _significant(quote, first=start)
    if not quoted:
        return Verdict(UNVERIFIED, "empty quote")
    if cited == quoted:
        return Verdict(PASS, "%s:%d-%d" % (path, start, end))
    if _dedented(cited) == _dedented(quoted):
        shift = _indent(quoted[0]) - _indent(cited[0])
        return Verdict(INDENT_DRIFT, "%s:%d-%d, quote reindented by %+d" % (path, start, end, shift))

    # A quote written on one line never claimed a layout, so the rule below -- which refuses a
    # quote whose indentation differs from the file's, because in Python that can change what the
    # code means -- has nothing to protect here. Judge it as the single run of text it was written
    # as, and only then fall through.
    if "\n" not in quote.strip() and end > start and _flat(cited) == _flat(quoted) and _flat(cited):
        return Verdict(REWRAPPED, "%s:%d-%d, quoted as one line" % (path, start, end))

    if [l.strip() for l in cited] == [l.strip() for l in quoted]:
        for n, (was, now) in enumerate(zip(cited, quoted)):
            if was != now:
                return Verdict(RETOUCHED, "%s:%d-%d, line %d whitespace altered (%d spaces in the "
                                          "file, %d in the quote)"
                               % (path, start, end, start + n, _indent(was), _indent(now)))

    # A quote reflowed onto one line: `ap.add_argument("--max-sleep", type=int, default=30, help=
    # "...")` for two lines of the file that say exactly that, wrapped. The model read the right
    # place and tidied it, which is not the same as inventing it, and refusing it as absent sends a
    # stage back to re-read a file it had already read correctly.
    if _flat(cited) == _flat(quoted) and _flat(cited):
        return Verdict(REWRAPPED, "%s:%d-%d, quote rewrapped onto %d line%s"
                       % (path, start, end, len(quoted), "" if len(quoted) == 1 else "s"))

    # Each quoted line inside the file's line at the same position in the run: a quote clipped out
    # of wrapped source, most often a string literal without the `deny("` that opens it. Short
    # fragments are not taken this way -- `allow()` appears in this file eleven times and would
    # vouch for anything.
    if (len(cited) == len(quoted) and len("".join(quoted)) >= 20
            and all(q.strip() and q.strip() in c for c, q in zip(cited, quoted))):
        return Verdict(CLIPPED, "%s:%d-%d, quoted without the line's opening text"
                       % (path, start, end))

    if _unslashed_quotes(cited) == _unslashed_quotes(quoted):
        return Verdict(ESCAPED, "%s:%d-%d, apostrophes escaped in the quote" % (path, start, end))

    # Part of the range, quoted: a claim about a block cites the block and quotes what it turns on.
    needle = _dedented(quoted)
    for i in range(len(cited) - len(needle) + 1):
        if _dedented(cited[i:i + len(needle)]) == needle:
            return Verdict(PARTIAL, "%s:%d-%d, quoted part of the range" % (path, start, end))

    # Right text, wrong address: worth distinguishing, because it means the model read the file and
    # mis-remembered where, rather than inventing the content. The line reported is the file's own,
    # found by the same search that locates a quote carrying no line numbers at all.
    where = locate(root, path, quote)
    if where:
        return Verdict(WRONG_LINES, "cited %s:%d-%d, text is at line %d"
                       % (path, start, end, where[0]))
    return Verdict(FAIL, "quote not present in %s" % path)


def locate(root: str, path: str, quote: str) -> tuple[int, int] | None:
    """Where in `path` the quoted text sits, if it sits there at all.

    A stage wrote ten findings as `EVIDENCE: <path>` and a run of QUOTE lines, with no line numbers
    anywhere, and every quote was verbatim. Line numbers are how a reader finds the text; the text
    being in the file is the thing that makes the claim true, and it can be established without
    them. Refusing this shape sent a correct ledger round again to add addresses.
    """
    full = os.path.join(root, path)
    if not os.path.isfile(full):
        return None
    try:
        lines = open(full, encoding="utf-8", errors="replace").read().split("\n")
    except OSError:
        return None
    # Real line numbers, so blank lines have to be carried through the match rather than dropped:
    # the first version searched the blank-stripped text and reported the offset in it, which read
    # as "cited line 167, text is near line 167" on a quote that was exactly where it said.
    kept = [(n, line.rstrip()) for n, line in enumerate(lines, 1) if line.strip()]
    haystack = [text for _, text in kept]
    needle = _dedented(_significant(quote))
    if not needle or len(needle) > len(haystack):
        return None
    for i in range(len(haystack) - len(needle) + 1):
        if _dedented(haystack[i:i + len(needle)]) == needle:
            return kept[i][0], kept[i + len(needle) - 1][0]
    if len("".join(needle)) < 20:
        return None
    for i in range(len(haystack) - len(needle) + 1):
        window = haystack[i:i + len(needle)]
        if all(q.strip() and q.strip() in c for c, q in zip(window, needle)):
            return kept[i][0], kept[i + len(needle) - 1][0]
    return None


_PATHISH = re.compile(r"[\w./~-]+\.[A-Za-z0-9_]+")


def path_named(root: str, raw: str) -> str | None:
    """The first token in `raw` that is the name of a file which exists."""
    for token in _PATHISH.findall(DECORATION.sub("", raw or "")):
        if os.path.isfile(os.path.join(root, token)):
            return token
    return None


def _unslashed_quotes(lines: list[str]) -> list[str]:
    """The same lines with a backslash before an apostrophe dropped, on both sides of a comparison."""
    return [re.sub(r"\\+'", "'", line) for line in lines]


def _flat(lines: list[str]) -> str:
    """One line of it, with every run of whitespace squeezed to a single space."""
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def command_result(calls, command_fragment: str, expected: str) -> Verdict:
    """Did some command in this session run `command_fragment` and print `expected`?"""
    seen = 0
    for call in calls:
        if call.tool != "Bash":
            continue
        if command_fragment not in str(call.args.get("command", "")):
            continue
        seen += 1
        if expected in (call.text or ""):
            return Verdict(PASS if call.ok else FAIL,
                           "%s (exit %s)" % (command_fragment, "ok" if call.ok else "error"))
    if not seen:
        return Verdict(UNVERIFIED, "no recorded command matching %r" % command_fragment)
    return Verdict(FAIL, "%d run(s) of %r, none printed %r" % (seen, command_fragment, expected))


def log_match(path: str, pattern: str) -> Verdict:
    """Is `pattern` present in `path`? Used for claims about server or runner behaviour."""
    full = os.path.expanduser(path)
    if not os.path.isfile(full):
        return Verdict(UNVERIFIED, "no such log: %s" % path)
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return Verdict(UNVERIFIED, "bad pattern: %s" % exc)
    with open(full, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            if rx.search(line):
                return Verdict(PASS, "%s:%d" % (path, n))
    return Verdict(FAIL, "no line of %s matches %r" % (path, pattern))

def absence(root: str, pattern: str, globs: str = "") -> Verdict:
    """Is `pattern` genuinely absent? The one claim a quote cannot support.

    Uses ripgrep because a claim of absence is only as good as the search behind it, and hand-rolled
    walking would quietly skip what .gitignore hides.
    """
    cmd = ["rg", "--no-heading", "-n", "-i", pattern]
    if globs:
        for g in globs.split(","):
            cmd += ["--glob", g.strip()]
    try:
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return Verdict(UNVERIFIED, "search failed: %s" % exc)
    if proc.returncode == 1:
        return Verdict(PASS, "%r not found%s" % (pattern, " in %s" % globs if globs else ""))
    if proc.returncode != 0:
        return Verdict(UNVERIFIED, "ripgrep exit %d: %s" % (proc.returncode, proc.stderr.strip()[:120]))
    hits = [l for l in proc.stdout.splitlines() if l.strip()]
    return Verdict(FAIL, "%d occurrence(s), e.g. %s" % (len(hits), hits[0][:110]))


# --- ledger parsing -------------------------------------------------------------------------
# The block form the gate asks for. Kept here rather than in the gate because the verifier is what
# defines a well-formed claim: anything this cannot parse is a claim the gate must refuse.

CLAIM_RE = re.compile(r"^CLAIM:\s*(?P<claim>.+?)\s*$", re.M)
UNKNOWN_RE = re.compile(r"^UNKNOWN:\s*(?P<unknown>.+?)\s*$", re.M)
_SAYS_UNKNOWN = re.compile(r"^\s*UNKNOWN\b\s*[:\u2014-]*\s*", re.I)
HEADERS = ("CLAIM:", "EVIDENCE:", "QUOTE:", "UNKNOWN:", "SEVERITY:", "FALSIFICATION:",
           "PREDICT:")

# `QUOTE (lines 212-217):` -- the header with the range said in passing before the colon. Written
# that way by a stage whose ledger was otherwise complete, and unrecognised as a header at all, so
# the quote never attached to anything and nine claims were reported as incomplete.
HEADER_RE = re.compile(r"^(?P<name>%s)\s*(?:\([^)]*\))?\s*:"
                       % "|".join(h.rstrip(":") for h in HEADERS))

# The four shapes an EVIDENCE line may take. Only the first was accepted originally, which quietly
# made two adapters impossible to satisfy: refactor-proposal requires an `absence` search and
# ops-perf a `log_match`, and a model writing blocks had nowhere to put either. The gate would then
# have demanded, every time, something the answer format could not express.
FILE_EV = re.compile(r"^(?P<path>\S+?):(?P<start>\d+)(?:-(?P<end>\d+))?$")
# The same citation with the decoration a model puts on a path, and anywhere in the line rather
# than alone on it. One whole review came back empty because every EVIDENCE line read
# `tools/trade_service.py:1925-1926` in backticks and the anchored form matched none of them --
# a refusal for punctuation, indistinguishable to the reader from a refusal for fabrication.
FILE_EV_ANY = re.compile(r"(?P<path>[^\s`*'\",]+?):(?P<start>\d+)(?:-(?P<end>\d+))?")
# "file.py:48, 345-348" -- one path, then further ranges that never repeat it. Written by the model
# in the run that found the lock-file claim, and refused for citing nothing.
MORE_RANGES = re.compile(r"(?<![:\w.-])(?P<start>\d+)-(?P<end>\d+)(?![\w.-])")
DECORATION = re.compile(r"[`*]+")
COMMAND_EV = re.compile(r"^command:\s*(?P<command>.+?)(?:\s*->\s*(?P<expect>.+?))?$", re.I)
ABSENCE_EV = re.compile(r"^absence:\s*(?P<pattern>.+?)(?:\s+in\s+(?P<globs>\S+))?$", re.I)
LOG_EV = re.compile(r"^log:\s*(?P<path>\S+)\s*~\s*(?P<pattern>.+)$", re.I)


# `cc-context-guard.py lines 174, 188-189, 212-213` -- the path and the lines, with everything but
# the colon. Written this way by every stage that has run here, and refused by a parser that wanted
# `path:174`, which is a quarrel about punctuation rather than about evidence.
# Any path-looking word: `scripts/guard.py`, `/private/tmp/r7tree/scripts/guard.py`, `guard.py`.
# Used only to find the file a CLAIM sentence is about, never to invent a citation.
ANY_PATH = re.compile(r"(?P<path>[^\s`*'\",()]+\.[A-Za-z0-9_]{1,6})\b")


PATH_LINES = re.compile(r"(?P<path>[^\s`*'\",]+\.[A-Za-z0-9_]+)\s*,?\s+lines?\s+"
                        r"(?P<ranges>\d{1,6}(?:\s*[-\u2013]\s*\d{1,6})?"
                        r"(?:\s*,\s*\d{1,6}(?:\s*[-\u2013]\s*\d{1,6})?)*)")
ONE_RANGE = re.compile(r"(?P<start>\d{1,6})(?:\s*[-\u2013]\s*(?P<end>\d{1,6}))?")

# `line 212-217 of scripts/cc-context-guard.py` -- the same citation with the halves the other way
# round, which is how a claim reads when the sentence begins with where rather than what.
LINES_PATH = re.compile(r"lines?\s+(?P<ranges>\d{1,6}(?:\s*[-\u2013]\s*\d{1,6})?"
                        r"(?:\s*,\s*\d{1,6}(?:\s*[-\u2013]\s*\d{1,6})?)*)"
                        r"\s+(?:of|in|from)\s+(?P<path>[^\s`*'\",]+\.[A-Za-z0-9_]+)")


# `guard.py, line 208: "# A stage put a test run in the background"` -- the quote on the citation's
# own line instead of under a QUOTE header. Only text in quotes or backticks is taken this way; the
# prose a claim ends with is not evidence and must not be compared against the file as though it
# were, which would turn "no citation" into "quote not present" and read as fabrication.
# The trailing full stop is allowed because a stage writes its citation as a sentence: `lines
# 221-222: `ap.add_argument("--max-sleep", type=int, default=30, ...)`.` -- one backticked span and
# a period, refused for the period as "incomplete file_quote" on a claim that had quoted the line.
# The text may not contain its own delimiter. Greedy, it ran from the first backtick to the last one
# on the line and swallowed the prose between them, so a claim that had cited two spans and
# described them was told its quote was "not present in" the file -- which reads as an accusation of
# fabrication, on the one shape of citation that is merely incomplete.
_INLINE_QUOTE = re.compile(r"^\s*[:,-]?\s*(?P<q>[\"'`])(?P<text>(?:(?!(?P=q)).)+)(?P=q)"
                           r"\s*[.;]?\s*$", re.S)


def _inline_quote(body: str, path: str, ranges: str) -> str | None:
    """The quoted text a citation is followed by on its own line, if that is all that follows.

    Read from the body as written rather than the copy with backticks and asterisks stripped: the
    stripping is there to see through decoration around a path, and run over a quote it eats the
    `*` out of `\\s*` and leaves a citation that cannot match the file it came from.
    """
    where = re.search(re.escape(path) + r"[^\n]*?" + re.escape(ranges), body)
    if where is None:
        return None
    found = _INLINE_QUOTE.match(body[where.end():])
    return found.group("text") if found else None


def _path_lines(body: str) -> list[dict]:
    """Citations written as a path followed by the lines, rather than path:line."""
    out = []
    plain = DECORATION.sub("", body)
    for found in LINES_PATH.finditer(plain):
        inline = _inline_quote(body, found.group("path"), found.group("ranges"))
        for span in ONE_RANGE.finditer(found.group("ranges")):
            start = int(span.group("start"))
            piece = {"kind": "file_quote", "path": found.group("path"), "start": start,
                     "end": int(span.group("end") or start)}
            if inline:
                piece["quote"] = inline
            out.append(piece)
    if out:
        return out
    for found in PATH_LINES.finditer(plain):
        inline = _inline_quote(body, found.group("path"), found.group("ranges"))
        for span in ONE_RANGE.finditer(found.group("ranges")):
            start = int(span.group("start"))
            piece = {"kind": "file_quote", "path": found.group("path"), "start": start,
                     "end": int(span.group("end") or start)}
            if inline:
                piece["quote"] = inline
            out.append(piece)
    return out


def _classify_all(body: str) -> list[dict]:
    """Every piece of evidence on one EVIDENCE line, decoration and all.

    A line can carry more than one citation -- "a.py:1-2 and a.py:40-41" is how the model writes a
    claim that rests on two places -- and taking only the first threw away half the evidence for it.
    Only file citations are split this way: a command or a glob may legitimately contain the words
    and punctuation this would split on.
    """
    single = _classify(body)
    if single is not None and single["kind"] != "file_quote":
        return [single]
    plain = DECORATION.sub("", body)
    found = [{"kind": "file_quote", "path": m.group("path"), "start": int(m.group("start")),
              "end": int(m.group("end") or m.group("start"))}
             for m in FILE_EV_ANY.finditer(plain)]
    if found:
        # Ranges after the last citation, carrying no path of their own, belong to the last path
        # named. Only those beyond it: the ones inside a citation are already accounted for.
        tail = plain[max(m.end() for m in FILE_EV_ANY.finditer(plain)):]
        last = found[-1]["path"]
        found += [{"kind": "file_quote", "path": last, "start": int(m.group("start")),
                   "end": int(m.group("end"))} for m in MORE_RANGES.finditer(tail)]
        return found
    spelled = _path_lines(body)
    if spelled:
        return spelled
    return [single] if single else []


def _classify(body: str) -> dict | None:
    m = FILE_EV.match(DECORATION.sub("", body))
    if m:
        return {"kind": "file_quote", "path": m.group("path"), "start": int(m.group("start")),
                "end": int(m.group("end") or m.group("start"))}
    m = COMMAND_EV.match(body)
    if m:
        return {"kind": "command_result", "command": m.group("command").strip(),
                "expect": (m.group("expect") or "").strip()}
    m = ABSENCE_EV.match(body)
    if m:
        return {"kind": "absence", "pattern": m.group("pattern").strip(),
                "globs": (m.group("globs") or "").strip()}
    m = LOG_EV.match(body)
    if m:
        return {"kind": "log_match", "path": m.group("path"), "pattern": m.group("pattern").strip()}
    return None


_ELISION = re.compile(r"^\s*(?:\.\.\.|\u2026|#\s*\.\.\.|//\s*\.\.\.|<\s*snip\s*>)\s*$")


def _elided(body: str) -> list[str]:
    """Split a quote on lines that are nothing but an elision marker."""
    pieces, current = [], []
    for line in _unfenced(body).split("\n"):
        if _ELISION.match(line):
            pieces.append("\n".join(current).strip("\n"))
            current = []
        else:
            current.append(line)
    pieces.append("\n".join(current).strip("\n"))
    return [p for p in pieces if p.strip()]


_MARKED = re.compile(r"^(?P<lead>[\s>*_#-]{0,6})(?P<name>%s)\s*(?:\([^)]*\))?\s*:(?P<body>.*)$"
                     % "|".join(h.rstrip(":") for h in HEADERS))
_EMPHASIS = re.compile(r"(\*\*|__|\*|_)")
# `**Finding 1: the rule is broader than its intent.**` -- what a review stage calls a claim when
# nobody has told it the word. A seven-finding report with a path and a line range under every one
# parsed as nought claims because of this word, and was refused for stating none.
_SYNONYM = re.compile(r"^(?P<lead>[\s>*_#-]{0,6})(?:finding|issue|claim)\s*#?\s*\d*\s*:", re.I)


def _starts_with_citation(line: str) -> bool:
    """Does this line open on a citation, rather than merely mention a path somewhere in prose?"""
    bare = DECORATION.sub("", line).strip().lstrip("'\"- ")
    return bool(PATH_LINES.match(bare) or LINES_PATH.match(bare) or FILE_EV_ANY.match(bare))


def normalise(text: str) -> str:
    """Undo the markdown a model puts on its headers, before the ledger is read line by line.

    A claims stage verified eight findings by running the guard and reporting what it printed, and
    the parser scored the answer nought claims, nought unknowns -- because every header arrived as
    `**CLAIM: the boundary is strict at 30 seconds.** `sleep 30` ... returned "allow"`. Bold is
    decoration, and the refusal it earned ("no claims were stated") described the one thing the
    stage had not done wrong.

    Two things happen here. The emphasis and bullet marks in front of a header are dropped, and a
    header emphasised inline is split at its closing mark, so the sentence after it is read as what
    it is rather than swallowed into the claim. If that remainder carries a citation it becomes an
    EVIDENCE line; prose that cites nothing is left alone, because inventing evidence for a claim
    is worse than reporting that it has none.
    """
    out: list[str] = []
    claimed = False                 # the last header emitted was a CLAIM, so a citation is its own
    for line in text.split("\n"):
        synonym = _SYNONYM.match(line)
        if synonym and not HEADER_RE.match(line):
            line = "%sCLAIM:%s" % (synonym.group("lead"), line[synonym.end():])
        seen = _MARKED.match(line)
        if not seen or not seen.group("lead").strip(" 	>#-"):
            # A citation on the line under a CLAIM, with no EVIDENCE header in front of it: the
            # shape every stage here writes when it has not been given the schema twice. It is a
            # citation either way, and dropping it reports a cited finding as citing nothing.
            if claimed and line.strip() and _starts_with_citation(line):
                out.append("EVIDENCE: %s" % line.strip())
                claimed = False
            else:
                out.append(line)
                claimed = claimed and not line.strip()
            continue
        mark = _EMPHASIS.search(seen.group("lead"))
        body, extra = seen.group("body"), ""
        if mark:
            closing = body.find(mark.group(1))
            if closing != -1:
                body, extra = body[:closing], body[closing + len(mark.group(1)):]
        out.append("%s: %s" % (seen.group("name"), body.strip()))
        claimed = seen.group("name") == "CLAIM"
        if extra.strip() and _classify_all(extra.strip()):
            out.append("EVIDENCE: %s" % extra.strip())
            claimed = False
    return "\n".join(out)


# `QUOTE: scripts/guard.py, lines 231-232: "    if OFF.exists():\n        allow()"` -- the citation
# on the QUOTE line and the text as a JSON string. Eighteen claims arrived in this shape, every one
# of them accurate, and all eighteen were judged as citing nothing: the EVIDENCE line above them
# described where in prose, so the only address in the block was the one inside the quote.
_LEADING_CITE = re.compile(r'^(?P<cite>[^"\']{0,240}?)\s*(?P<q>["\'])')
# `\'` is not an escape in JSON, and a model writing a quote full of regexes produces it. The decode
# then fails and the quote keeps the stray backslash, so an eleven-line citation that was correct to
# the character came back as "quote not present in" -- over one character it had added, not moved.
_BAD_ESCAPE = re.compile(r"(?<!\\)\\'")


def _quote_carrying_citation(body: str) -> tuple[list[dict], str]:
    """Split a QUOTE body into the citation it opens with and the quoted text after it.

    The text is decoded as a JSON string when it is one, which is the only way to tell the newline
    the model meant as a line break from the `\\n` inside a regex it quoted. Guessing between them
    corrupts either the layout or the code.
    """
    seen = _LEADING_CITE.match(body)
    if not seen:
        return [], body
    found = _classify_all(seen.group("cite"))
    if not found:
        return [], body
    rest = body[seen.start("q"):]
    if rest.startswith('"'):
        for candidate in (rest, _BAD_ESCAPE.sub("'", rest)):
            try:
                text, _ = json.JSONDecoder().raw_decode(candidate)
                return found, text
            except ValueError:
                continue
    span = _first_span(rest)
    if span is None:
        return found, rest
    # Single-quoted, which JSON has no opinion about: the same string, escaped the way Python would
    # write it. Left as it arrived, `\n` stays two characters and the quote is one long line that
    # matches nothing.
    return found, _unescaped(span)


def _first_span(text: str) -> str | None:
    """The first quoted run in `text`, honouring backslash escapes, ignoring whatever follows it.

    Not anchored to the end of the line, because a citation is often followed by the point it is
    making: `... % max(naps))' -- no off-switch reference.` The prose after the closing quote is
    commentary, and taking it as part of the quote fails the comparison against the file.
    """
    if not text or text[0] not in "\"'":
        return None
    delim, i = text[0], 1
    out: list[str] = []
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            out.append(text[i:i + 2])
            i += 2
            continue
        if text[i] == delim:
            return "".join(out)
        out.append(text[i])
        i += 1
    return None


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "'": "'", "\\": "\\"}


def _unescaped(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text) and text[i + 1] in _ESCAPES:
            out.append(_ESCAPES[text[i + 1]])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def parse_ledger(text: str, root: str = "") -> tuple[list[dict], list[str]]:
    """Split an answer into claims and declared unknowns.

    Scanned line by line rather than matched with one regex, because a QUOTE runs until the next
    header and quoted code contains blank lines, colons and anything else a regex would trip on.
    The rule is simply: a quote ends at the next line beginning with a known header, or at the end.
    """
    text = normalise(text)
    claims: list[dict] = []
    current: dict | None = None
    evidence: dict | None = None
    cited: list[dict] = []
    quote: list[str] | None = None
    claim_path: str | None = None

    def close_quote() -> None:
        nonlocal quote, evidence, claim_path
        if quote is None:
            quote = None
            return
        body = "\n".join(quote).strip("\n")
        carried, spoken = _quote_carrying_citation(body)
        if carried and evidence is not None:
            # The QUOTE line's own citation, whether or not the EVIDENCE line above it named one.
            # Only filling this in when EVIDENCE named nothing left the address sitting inside the
            # quoted text whenever a stage said where twice, so the comparison ran the citation
            # against the file and reported the claim as unquoted.
            for key, value in carried[0].items():
                if key != "quote" and not evidence.get(key):
                    evidence[key] = value
            body = spoken
        if evidence is not None and not evidence.get("start"):
            trailing = [_TRAILING_LINE.match(l) for l in body.split("\n") if l.strip()]
            if trailing and all(trailing):
                numbers = [int(m.group("n")) for m in trailing]
                evidence["start"], evidence["end"] = min(numbers), max(numbers)
                evidence.setdefault("kind", "file_quote")
                body = _untrailed(body)
        if evidence is not None and not evidence.get("path") and claim_path:
            evidence["path"] = claim_path
            evidence.setdefault("kind", "file_quote")
        if (root and evidence is not None and not evidence.get("path")
                and evidence.get("start") and body.strip()):
            # Lines and the text, and no file named anywhere: a ledger about one file says which
            # file once, in the first claim, and a person reading the fifth one knows what it is
            # about. Ask the tree instead of guessing -- the answer is only taken when exactly one
            # file holds that text at those lines, which is a stricter test than a typed path.
            where = resolve_path(root, evidence["start"],
                                 evidence.get("end") or evidence["start"], _untrailed(body))
            if where:
                evidence["path"] = where
                evidence.setdefault("kind", "file_quote")
        pieces = _elided(body)
        if len(cited) > 1 and len(pieces) == len(cited):
            # One EVIDENCE line naming two ranges, quoted with an elision between them: the
            # definition and the use, which is the citation an architecture review actually wants
            # to make. Attaching the whole block to the last range failed both -- "quote not
            # present" on one and "incomplete" on the other -- on a claim that was correct.
            for citation, piece in zip(cited, pieces):
                citation["quote"] = piece
        elif evidence is not None:
            evidence["quote"] = body
        quote = None

    for line in text.split("\n"):
        seen = HEADER_RE.match(line)
        header = seen.group("name") + ":" if seen else None
        if quote is not None and header is None:
            quote.append(line)
            continue
        close_quote()
        if header == "CLAIM:":
            current = {"claim": line[seen.end():].strip(), "evidence": [],
                       "severity": None, "falsification": None}
            claims.append(current)
            evidence = None
            # A claim that says which file it is about, quoting underneath with only line numbers,
            # is how a stage writes a review of one file: naming the path again on every quote is
            # repetition a person would not write either. Eleven citations of one file were reported
            # as "missing a file and a line range" when the file was named in the sentence above.
            named = ANY_PATH.search(DECORATION.sub("", current["claim"]))
            claim_path = named.group("path") if named else None
        elif current is None:
            continue
        elif header == "EVIDENCE:":
            body = line[seen.end():].strip()
            found = _classify_all(body)
            if not found:
                found = [{"kind": None, "raw": body}]
            current["evidence"].extend(found)
            cited = found
            # A QUOTE that follows attaches to the last citation named, which is the one it is
            # under. The earlier ones are still checked, on their line numbers alone.
            evidence = found[-1]
        elif header == "QUOTE:":
            if evidence is None:
                # A QUOTE with no EVIDENCE line above it. The citation it carries is the only
                # address the claim has, and dropping the whole block on a missing header reports a
                # cited claim as citing nothing.
                evidence = {"kind": "file_quote"}
                current["evidence"].append(evidence)
                cited = [evidence]
            elif evidence.get("quote"):
                # A second QUOTE under the same EVIDENCE is a second piece of evidence, not a
                # correction of the first. Overwriting kept only the last of ten quoted lines and
                # judged the claim on it, which is how a ledger of verbatim quotes came back as one
                # quote that did not match.
                sibling = {k: v for k, v in evidence.items() if k != "quote"}
                sibling.pop("start", None)
                sibling.pop("end", None)
                current["evidence"].append(sibling)
                evidence = sibling
                cited = [sibling]
            quote = []
            rest = line[seen.end():].strip()
            if rest:
                quote.append(rest)
        elif header is None and evidence is not None and _classify_all(line.strip()):
            # A citation on a line of its own, under an EVIDENCE header that named another. Five
            # arrived that way in one block and only the first was read; the rest were dropped, and
            # the claim they supported was reported as citing nothing.
            more = _classify_all(line.strip())
            current["evidence"].extend(more)
            cited = more
            evidence = more[-1]
        elif header == "SEVERITY:":
            current["severity"] = line[seen.end():].strip().lower()
        elif header == "FALSIFICATION:":
            current["falsification"] = {"command": line[seen.end():].strip()}
    close_quote()

    for claim in claims:
        for piece in claim["evidence"]:
            if piece.get("kind") is not None or not root or not piece.get("quote"):
                continue
            named = path_named(root, piece.get("raw", ""))
            if named and not BARE_LINES.search(piece.get("raw", "")):
                # A path and a quote, no line numbers. Where it is gets worked out from the quote.
                piece.update(kind="file_quote", path=named)
                continue
            where = BARE_LINES.search(piece.get("raw", ""))
            if not where:
                continue
            start = int(where.group("start"))
            end = int(where.group("end") or start)
            path = resolve_path(root, start, end, piece["quote"])
            if path:
                piece.update(kind="file_quote", path=path, start=start, end=end)

    # The single-file_quote view the verifier and its tests were written against.
    for claim in claims:
        first = next((e for e in claim["evidence"] if e.get("kind") == "file_quote"), {})
        claim.update({"path": first.get("path"), "start": first.get("start"),
                      "end": first.get("end"), "quote": first.get("quote")})
    # A claim that says UNKNOWN is an unknown, whichever header it arrived under. Two came back as
    # `CLAIM: UNKNOWN: I could not verify ...` and were refused for citing nothing -- for saying the
    # one thing the stance calls a complete answer, in the wrong place.
    unknowns = [m.group("unknown") for m in UNKNOWN_RE.finditer(text)]
    declared = [c for c in claims if _SAYS_UNKNOWN.match(c["claim"])]
    for claim in declared:
        unknowns.append(_SAYS_UNKNOWN.sub("", claim["claim"]).strip() or claim["claim"])
        claims.remove(claim)
    return claims, unknowns


PREDICT_RE = re.compile(r"^PREDICT:\s*(?P<body>.+?)\s*$", re.M)


def predictions(text: str) -> list[dict]:
    """The failing runs a plan commits to before a line of the change is written.

    Same grammar as command evidence, and deliberately so: a prediction is a claim about a run that
    has not happened, and it is checked later by exactly the machinery that checks one that has.
    Anything that does not parse as a command with an expected output is dropped rather than kept
    as prose, because prose is what this exists to replace.
    """
    out = []
    for m in PREDICT_RE.finditer(text):
        found = _classify(m.group("body"))
        if found and found.get("kind") == "command_result" and found.get("expect"):
            out.append(found)
    return out


# "line 174", "lines 208-211" -- a citation that names where it read but not what it read.
BARE_LINES = re.compile(r"\b[Ll]ines?\s+(?P<start>\d{1,6})(?:\s*[-\u2013]\s*(?P<end>\d{1,6}))?")


def _tracked(root: str) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True, text=True,
                             timeout=20)
    except (OSError, subprocess.SubprocessError):
        return []
    return [l for l in out.stdout.splitlines() if l.strip()] if out.returncode == 0 else []


def resolve_path(root: str, start: int, end: int, quote: str) -> str | None:
    """Which file the quote came from, when the citation named lines but no file.

    A stage wrote sixteen claims, every one of them carrying real quoted source and a line number,
    and every one was refused for citing nothing -- it had written "line 212 checks" instead of
    naming the file. Asking the tree which file holds that text at that line is a stricter test
    than believing a path the model typed, and it is the same comparison either way: the answer is
    only accepted when exactly one file matches, so an ambiguous quote is still uncited.
    """
    hits = [path for path in _tracked(root) if file_quote(root, path, start, end, quote).ok]
    return hits[0] if len(hits) == 1 else None


def verify_ledger(root: str, text: str) -> list[tuple[dict, Verdict]]:
    out = []
    for claim in parse_ledger(text, root)[0]:
        if not claim["path"] or claim["quote"] is None:
            out.append((claim, Verdict(UNVERIFIED, "claim has no EVIDENCE/QUOTE block")))
            continue
        out.append((claim, file_quote(root, claim["path"], claim["start"], claim["end"],
                                      claim["quote"])))
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--ledger", help="file containing CLAIM/EVIDENCE/QUOTE blocks; - for stdin")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not args.ledger:
        ap.error("--ledger is required")
    text = sys.stdin.read() if args.ledger == "-" else open(args.ledger).read()

    results = verify_ledger(args.root, text)
    if args.json:
        json.dump([{"claim": c["claim"], "verdict": v.kind, "detail": v.detail}
                   for c, v in results], sys.stdout, indent=2)
        print()
    else:
        for i, (claim, verdict) in enumerate(results, 1):
            print("%d. %s\n   %s" % (i, claim["claim"][:96], verdict))
        bad = [v for _, v in results if not v.ok]
        print("\n%d claim(s), %d unsupported" % (len(results), len(bad)))
    return 1 if any(not v.ok for _, v in results) else 0


if __name__ == "__main__":
    sys.exit(main())
