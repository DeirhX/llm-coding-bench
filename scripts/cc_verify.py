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


def under_root(root: str, path: str) -> str:
    """A path inside the tree, named relative to it however the model wrote it.

    A stage citing `/private/tmp/r17tree/scripts/guard.py` for a tree opened as `/tmp/r17tree` names
    the same file, through the symlink macOS puts on /tmp. Left absolute it verifies and then fails
    the coverage check, which compares against what the reads recorded.
    """
    if not root or not os.path.isabs(path):
        return path
    try:
        real = os.path.realpath(path)
        base = os.path.realpath(root)
    except OSError:
        return path
    inside = os.path.relpath(real, base)
    return path if inside.startswith("..") else inside


def _other_reading(quote: str) -> str | None:
    r"""The same quote with `\n` read the other way, if there is another way to read it.

    A quote that arrived as a JSON string has already had its escapes decoded, and for `\n` that
    decision cannot be made from the text: it is a line break when the model handed over a run of
    lines as one string, and two characters when it quoted a character class. Deciding it in the
    parser is guessing. The file settles it, so the parser hands over one reading and this supplies
    the other.
    """
    if "\n" in quote:
        return quote.replace("\n", "\\n")
    if "\\n" in quote:
        return quote.replace("\\n", "\n")
    return None


def _closer(first: Verdict, second: Verdict) -> Verdict:
    """The more useful of two readings: one that holds, else the one that says where it looked."""
    for candidate in (first, second):
        if candidate.ok:
            return candidate
    told = {FAIL: 0, UNVERIFIED: 1}
    return second if told.get(second.kind, 2) > told.get(first.kind, 2) else first


def file_quote(root: str, path: str, start: int, end: int, quote: str) -> Verdict:
    """Do lines [start, end] of `path` contain `quote`, allowing for reindentation?

    Both readings of an ambiguous `\n` are tried, because refusing a correct quote costs a round and
    trying twice costs a string comparison.
    """
    verdict = _judged_quote(root, path, start, end, quote)
    other = None if verdict.ok else _other_reading(quote)
    if other is None:
        return verdict
    return _closer(verdict, _judged_quote(root, path, start, end, other))


def _judged_quote(root: str, path: str, start: int, end: int, quote: str) -> Verdict:
    path = under_root(root, path)
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

    if (_unslashed_quotes(cited) == _unslashed_quotes(quoted)
            or _unslashed_quotes(_dedented(cited)) == _unslashed_quotes(_dedented(quoted))):
        # Dedented as well, because the two happen together: a stage quoting an indented line into
        # JSON drops the indentation and unescapes a quote character in the same breath, and the
        # escape tolerance never fired for any real citation until it allowed for that.
        return Verdict(ESCAPED, "%s:%d-%d, quotes escaped differently in the quote"
                       % (path, start, end))

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
    """The same lines with a backslash before a quote dropped, on both sides of a comparison.

    Double quotes for the same reason as apostrophes, and from the same stage: writing its quotes as
    JSON, it gave the file's `[^\\s\'\\";|&]` as `[^\\s\'";|&]`, having unescaped one character on the
    way out. Eleven citations of a regex were unresolvable over that backslash -- not failed, which
    would at least have named the line, but unattributable to any file at all.
    """
    return [re.sub(r"\\+(['\"])", r"\1", line) for line in lines]


def _flat(lines: list[str]) -> str:
    """One line of it, with every run of whitespace squeezed to a single space."""
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


_STOPWORDS = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "with", "no", "not", "is",
              "was", "it", "its", "that", "this", "for", "as", "at", "by", "from", "so", "output",
              "exit", "code", "printed", "prints", "returns", "returned", "result"}


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z0-9_]+", text.lower())
            if w not in _STOPWORDS and len(w) > 1]


# Words that describe an outcome by what did not happen. A probe of a rule that stops things reports
# a stop by showing the refusal; a probe of the same rule letting something through has nothing to
# show but silence, and says so in whatever words fit the sentence. "is not caught by _CALLS" was
# four such reports in run 25, each against a hook that printed nothing, each judged a failure.
_SILENCE = re.compile(r"\b(no|not|nothing|silent|silence|empty|without|allow(?:ed|s)?|permitted|"
                      r"uncaught|unmatched)\b", re.I)
# What a shell says when a command said nothing: the client's placeholder, and the exit marker a
# stage adds to see the status. A probe of a hook that allows prints exactly this and nothing else.
_QUIET = re.compile(r"^(?:\s*|-{2,}|EXIT:\s*\d+|\(?[Bb]ash completed with no output\)?|"
                    r"\(?no output\)?)$")


def _said(printed: str) -> str:
    """What the command actually printed, with the shell's bookkeeping taken out."""
    return "\n".join(l for l in (printed or "").splitlines() if not _QUIET.match(l.strip())).strip()


def _supports(printed: str, expected: str) -> bool:
    """Does what the command printed bear out how the stage described it?

    Verbatim substring was the first rule and it refused a stage that had run the experiment and
    reported it correctly: it wrote "DENIED (hookSpecificOutput with permissionDecision: deny)" of
    output that says exactly that in JSON, and "ALLOWED (no deny output, exit code 0)" of a command
    that printed nothing at all. Neither description is a quote and neither is wrong. Descriptions
    are held to their content words instead, most of which must appear in the output, and a
    description of silence is held to the output being silent.
    """
    if expected.strip() and expected in printed:
        return True
    body = _said(printed)
    wanted = _tokens(expected)
    if not body:
        # "no deny output", "exit code 0", "nothing printed": a claim about silence, against
        # silence. Anything that names something the command supposedly said is not that.
        return bool(_SILENCE.search(expected)) or not wanted
    if not wanted:
        return False
    low = body.lower()
    # "DENIED" of output that says `"permissionDecision": "deny"`, "ALLOWED" of one that says
    # allow: the stage described what it saw in the words a person uses for it. Long words are
    # matched on their opening so that the tense does not decide whether a true report is accepted.
    hits = sum(1 for w in set(wanted) if w in low or (len(w) >= 5 and w[:3] in low))
    if _SILENCE.search(expected):
        # A description with "no" or "nothing" in it is partly about what is absent, and counting
        # words cannot see a negation: "no failures, nothing printed" scored a match against "3
        # failed, 40 passed", on the strength of failures against failed. Every word has to be
        # there before a claim of that shape is accepted against output that is not silent.
        return hits == len(set(wanted))
    return hits * 2 >= len(set(wanted))


# `"command": "touch /tmp/cc-guard-off"` -- the payload inside a probe of a hook. Twenty probes in
# one session differ only here; everything around them is the same echo, the same pipe and the same
# path, so this is the part that says which experiment a citation is about.
_PAYLOAD = re.compile(r'"(?:command|file_path|new_string|content)"\s*:\s*"(?P<value>[^"]{3,})"')


# An environment assignment in the shell around a probe. Three characters at least, so that the
# throwaway `X=cc-guard` inside a payload under test is not read as the condition of the experiment.
_ASSIGNED = re.compile(r"(?<![\w.-])[A-Z_][A-Z0-9_]{2,}=[^\s;&|)'\"]*")


def _settings(text: str) -> set[str]:
    """Environment assignments in the shell around a probe, which its payload does not carry."""
    return set(_ASSIGNED.findall(_PAYLOAD.sub("", text)))


def _same_command(wanted: str, ran: str) -> bool:
    """Is the cited command one of the things this call ran?

    A stage testing six payloads runs them in one loop and cites them one at a time, which is the
    right way round to report it: the loop is how it was run, the payload is what the claim is
    about. Content-word overlap alone said yes to all twenty, because probes of the same hook are
    nearly the same string -- so when the citation carries a payload, that payload has to be in
    what ran, and it is what distinguishes them.
    """
    payloads = [m.group("value") for m in _PAYLOAD.finditer(wanted)]
    if payloads:
        if not all(" ".join(v.split()) in ran for v in payloads):
            return False
        # The payload is what the hook was asked about; the shell around it can be the entire claim.
        # Run 21 cited `(touch /tmp/cc-guard-off; export CC_GUARD_LIFTABLE=1) && echo <payload>` and
        # was matched to twelve recorded probes carrying that payload, none of which set the
        # variable -- the one condition the claim was about. It passed, on a command nobody ran.
        return _settings(wanted) <= _settings(ran)
    words = set(_tokens(wanted))
    if len(words) < 4:
        return False
    present = set(_tokens(ran))
    return len(words & present) * 10 >= len(words) * 7


# Where the next case starts in the output of a loop: a rule of dashes, or a label the stage echoed
# before each one. EXIT is deliberately not a label -- it is the status of the case just run, and
# treating it as the start of the next one empties every section.
_NEXT_CASE = re.compile(r"^\s*(?:-{3,}|={3,}|(?!EXIT)[A-Z][A-Z_]{1,9}:)", re.M)


def _section(printed: str, payloads: list[str]) -> str:
    """The part of the output belonging to the payload cited, when several ran in one call.

    Five probes in one loop print five results, and judging a claim about the third against all
    five would accept "denied" from a run that allowed. The stage labels each case as it goes,
    which is what makes the split possible; when it does not, the whole output stands.
    """
    # Only a payload long enough to be its own address. `"content": "off"` is a payload too, and
    # looking for "off" in the output found it inside "off-switches" in the refusal text, so the
    # section began in the middle of the sentence that proved the claim.
    for value in sorted((v for v in payloads if len(v) >= 8), key=len, reverse=True):
        at = printed.find(value)
        if at < 0:
            continue
        rest = printed[at + len(value):]
        stop = _NEXT_CASE.search(rest)
        return rest[:stop.start()] if stop else rest
    return printed


# What a claim looks like when the point of the probe was that it was not allowed to run.
_CLAIMS_REFUSAL = re.compile(r"\b(denied|denies|deny|refused|refuses|refusal|blocked|blocks|"
                             r"rejected|not permitted|forbidden)\b", re.I)
# An ordinary non-zero exit, which is a command that ran and failed -- not a command that was stopped.
_SHELL_FAILURE = re.compile(r"\bexit code\s+\d+|command not found|No such file or directory", re.I)


def _matching(calls, wanted: str):
    """The recorded Bash calls a citation of `wanted` could be about."""
    for call in calls:
        if call.tool != "Bash":
            continue
        ran = " ".join(str(call.args.get("command", "")).split())
        if wanted not in ran and ran not in wanted and not _same_command(wanted, ran):
            continue
        if not _settings(wanted) <= _settings(ran):
            continue
        yield call


def _one_line(text: str, room: int = 200) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= room else flat[:room - 3] + "..."


def command_result(calls, command_fragment: str, expected: str) -> Verdict:
    """Did some command in this session run `command_fragment` and print `expected`?

    The fragment is matched loosely in both directions because a stage testing six payloads runs
    them in one call, joined by semicolons or wrapped in a loop, and then cites them one at a time
    -- which is the right way round to report it and was reported as five commands nobody ran.
    """
    wanted = " ".join(command_fragment.split())
    if not expected.strip():
        # `EVIDENCE: command: <cmd>` with no `-> <output>` asserts nothing a session can be held to,
        # and the blank sailed through every check because "" is a substring of anything. Run 21's
        # sixth claim passed that way, against a command that was never run.
        #
        # The output is on record, so the refusal shows it rather than describing what is missing.
        # Run 22's third round was refused thirteen times for this, every gap reading the same, and
        # a stage that has run the experiment and cannot guess the format it is wanted in will lose
        # the round again. Quoting it back makes the correction mechanical -- and it is the stage's
        # own transcript, so nothing here tells it anything it did not do.
        ran = [_said(call.text or "") for call in _matching(calls, wanted)]
        spoke = next((r for r in ran if r.strip()), "")
        if spoke:
            return Verdict(UNVERIFIED, "the citation says what was run and not what it printed. It "
                                       "printed: %s -- put `-> ` and the part of that which bears "
                                       "the claim out after the command"
                           % _one_line(spoke))
        if ran:
            return Verdict(UNVERIFIED, "the citation says what was run and not what it printed. It "
                                       "printed nothing at all -- write `-> ` and say so, if that "
                                       "silence is what makes the claim true")
        return Verdict(UNVERIFIED, "the citation says what was run and not what it printed; add "
                                   "-> <text it printed> so there is something to check")
    seen = 0
    for call in calls:
        if call.tool != "Bash":
            continue
        ran = " ".join(str(call.args.get("command", "")).split())
        if wanted not in ran and ran not in wanted and not _same_command(wanted, ran):
            continue
        if not _settings(wanted) <= _settings(ran):
            # `ran in wanted` admits a recorded call that is part of the cited one, which is how a
            # citation gets credit for a command nobody ran: run 21 cited
            # `export CC_GUARD_LIFTABLE=1; echo <payload> | guard` and the recorded call was the
            # `echo` alone. The variable was the experiment.
            continue
        seen += 1
        # A probe of a rule that stops things is reported by being stopped: the refusal is the result,
        # and the text on record is the hook's message, which does not contain the word `denied` the
        # claim uses. Judged on word overlap it failed, and the verdict was FAIL regardless because
        # the call did not succeed -- so a stage reviewing a deny rule could not cite the denial that
        # proves it works. Run 20's claims stage was refused 19 times gathering exactly this.
        text = call.text or ""
        if not call.ok and _CLAIMS_REFUSAL.search(expected) and not _SHELL_FAILURE.search(text):
            return Verdict(PASS, "%s -- stopped, as the claim says" % command_fragment[:60])
        payloads = [m.group("value") for m in _PAYLOAD.finditer(wanted)]
        if _supports(_section(call.text or "", payloads), expected):
            return Verdict(PASS if call.ok else FAIL,
                           "%s (exit %s)" % (command_fragment[:60], "ok" if call.ok else "error"))
    if not seen:
        return Verdict(UNVERIFIED, "no recorded command matching %r" % command_fragment[:80])
    return Verdict(FAIL, "%d run(s) of %r, none printed anything like %r"
                   % (seen, command_fragment[:60], expected[:60]))


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
# QUOTES and RUN are not in the contract; a stage reviewing the guard wrote both, having three
# quotes to give for one claim and six commands it had actually run. Nine correct findings were
# reported as citing nothing. Neither form is vaguer than the ones asked for -- a run of quoted
# lines each carrying its own line number, and a command with what it printed -- so both are read,
# and both are checked exactly as the canonical spelling is.
HEADERS = ("CLAIM:", "EVIDENCE:", "QUOTES:", "QUOTE:", "UNKNOWN:", "SEVERITY:", "FALSIFICATION:",
           "PREDICT:", "RUN:")

# `QUOTE (lines 212-217):` -- the header with the range said in passing before the colon. Written
# that way by a stage whose ledger was otherwise complete, and unrecognised as a header at all, so
# the quote never attached to anything and nine claims were reported as incomplete.
# Case-insensitive because a stage writing a report writes `Evidence:`, not `EVIDENCE:`. Matched
# only at the start of a line and only before a colon, which is where a header lives and where prose
# does not put these words. 188 claims cited nothing over the case of one letter.
HEADER_RE = re.compile(r"^(?P<name>%s)\s*(?:\([^)]*\))?\s*:"
                       % "|".join(h.rstrip(":") for h in HEADERS), re.I)

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
        return {"kind": "command_result", "command": m.group("command").strip().strip("`").strip(),
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


# The closing emphasis before the colon is optional because `**CLAIM 1**: ...` puts it there, and
# `**CLAIM 1:** ...` does not. Eight findings written the first way parsed as none, and the stage was
# told it had stated no claims after 138 tool calls -- the most misleading refusal this can produce,
# because the work was done and written down in the shape the contract asks for, bar two asterisks.
_MARKED = re.compile(r"^(?P<lead>[\s>*_#-]{0,6})(?P<name>%s)\s*(?:\([^)]*\))?\s*[*_]{0,2}\s*"
                     r":(?P<body>.*)$" % "|".join(h.rstrip(":") for h in HEADERS), re.I)
_EMPHASIS = re.compile(r"(\*\*|__|\*|_)")
# `**Finding 1: the rule is broader than its intent.**` -- what a review stage calls a claim when
# nobody has told it the word. A seven-finding report with a path and a line range under every one
# parsed as nought claims because of this word, and was refused for stating none.
_SYNONYM = re.compile(r"^(?P<lead>[\s>*_#-]{0,6})(?:finding\s*#?\s*\d+|(?:issue|claim)\s*#?\s*\d*)"
                      r"\s*[*_]{0,2}\s*:", re.I)
# A numbered finding is a heading. A bare `FINDING:` under an EVIDENCE block is the conclusion drawn
# from it -- the same claim said again, in the stage's own words. Read as a claim of its own it had
# no evidence under it, so a report of four findings scored eight claims, half of them citing
# nothing, and the arithmetic made an accurate report look like a half-empty one.
# A line that says which of its sentences is a finding: an explicit header, a numbered item, or one of
# the words a report uses for the same thing. Its presence anywhere means the answer marks its claims.
_MARKS = re.compile(r"^[\s>*_#-]{0,6}(?:claim|unknown)\b|^[\s>*_#-]{0,6}\d{1,2}[.)]\s|"
                    r"^[\s>*_#-]{0,6}finding\s*\d", re.I)
_CONCLUSION = re.compile(r"^[\s>*_#-]{0,6}(?:finding|conclusion|verdict)s?\s*:", re.I)

# `CLAIMS` on a line of its own, and then `**1. The off-switch can still be turned on.**` -- the word
# said once as a heading and the findings numbered under it, which is how a person writes a report.
# Nothing in it says CLAIM, so a stage that made 188 of these parsed as none and was told no claims
# were stated. Only numbered under such a heading: a numbered list in prose is a numbered list.
_SECTION = re.compile(r"^[\s>*_#]{0,6}(?:claims?|findings?)\s*:?\s*[*_#]{0,3}\s*$", re.I)
_NUMBERED = re.compile(r"^(?P<lead>[\s>*_#-]{0,6})(?P<n>\d{1,3})[.)]\s+(?P<rest>\S.*)$")


# `Evidence: line 280-281 of `guard.py`` at the end of a paragraph. Only where it introduces
# something that classifies as a citation, so a sentence about the evidence stays a sentence.
_SAID_EVIDENCE = re.compile(r"\bevidence\s*:", re.I)


def _starts_with_citation(line: str) -> bool:
    """Does this line open on a citation, rather than merely mention a path somewhere in prose?"""
    bare = DECORATION.sub("", line).strip().lstrip("'\"- ")
    return bool(PATH_LINES.match(bare) or LINES_PATH.match(bare) or FILE_EV_ANY.match(bare))


# "`OFF_SWITCH.exists() and ...` at line 323", "`_VERBS = r\"...\"` at lines 267-270": the shape a
# stage writes when it is explaining rather than filling in a form. The quote and the line are both
# there, in the order a sentence puts them, and nothing read either.
_QUOTED_AT_LINE = re.compile(r"`(?P<quote>[^`]{6,400})`[^`\n]{0,40}?\bat\s+lines?\s+"
                             r"(?P<start>\d{1,6})(?:\s*[-\u2013]\s*(?P<end>\d{1,6}))?")


def _quoted_at_line(body: str) -> list[dict]:
    """Quotations of the file that say which line they came from, mid-sentence."""
    out = []
    for found in _QUOTED_AT_LINE.finditer(body):
        start = int(found.group("start"))
        out.append({"kind": "file_quote", "path": None, "start": start,
                    "end": int(found.group("end")) if found.group("end") else start,
                    "quote": found.group("quote")})
    return out


def _cites(rest: str) -> bool:
    """Whether what follows the word `evidence` in a sentence is a citation of any admissible kind.

    Asked of file citations only, a paragraph ending `EVIDENCE: echo ... | python3 guard.py produced
    no output (ALLOW)` keeps the probe inside the claim, and eight findings that had each run their
    probe were refused for citing nothing.
    """
    return bool(_classify_all(rest) or _bare_command(rest) or _probes(rest)
                or _quoted_at_line(rest))


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
    # Whether the answer marks its findings at all. An answer that marks some of them is read as
    # meaning the unmarked paragraphs are prose; only in one that marks none is a paragraph under a
    # claims heading read as the finding it states, which is the shape a bare `CLAIM` heading leaves.
    unmarked = not any(_MARKS.match(line) and not _SECTION.match(line)
                       for line in text.split("\n"))
    claimed = False                 # the last header emitted was a CLAIM, so a citation is its own
    listing = False                 # a CLAIMS heading has been seen, so numbered items are claims
    opening = True                   # this line begins a paragraph, so under a heading it is a claim
    for line in text.split("\n"):
        if _SECTION.match(line):
            listing = True
            out.append(line)
            continue
        numbered = _NUMBERED.match(line) if listing else None
        if numbered and not HEADER_RE.match(line) and not _SYNONYM.match(line):
            line = "%sCLAIM: %s" % (numbered.group("lead"), numbered.group("rest"))
        elif (listing and unmarked and opening and line.strip() and not HEADER_RE.match(line)
              and not _SYNONYM.match(line) and not _CONCLUSION.match(line)):
            # Under a bare `CLAIM` heading, one finding to a paragraph and no marker on any of them.
            # A stage wrote four that way, each with its probe in the last sentence, and the answer
            # scored no claims at all -- the heading having said the word once for all of them.
            line = "CLAIM: %s" % line.strip()
        opening = not line.strip()
        if _CONCLUSION.match(line) and not HEADER_RE.match(line) and not _SYNONYM.match(line):
            out.append(line)
            continue
        synonym = _SYNONYM.match(line)
        if synonym and not HEADER_RE.match(line):
            line = "%sCLAIM:%s" % (synonym.group("lead"), line[synonym.end():])
        seen = _MARKED.match(line)
        if seen and seen.group("lead").strip() and not seen.group("lead").strip(" 	>#-"):
            # `## CLAIM: ...`, a header written as a markdown heading, which is how a stage writes a
            # report it thinks of as a document. The emphasis path below cannot help -- there is no
            # emphasis -- and nothing else takes the hashes off, so the header matcher never sees a
            # header. Run 22's closing message was nine headings of exactly this shape and parsed as
            # nought claims, which is also why the fabricated quotes in it went unnoticed.
            line = line[seen.start("name"):]
            seen = _MARKED.match(line)
        if not seen or not seen.group("lead").strip(" 	>#-"):
            # A citation on the line under a CLAIM, with no EVIDENCE header in front of it: the
            # shape every stage here writes when it has not been given the schema twice. It is a
            # citation either way, and dropping it reports a cited finding as citing nothing.
            if (claimed and line.strip() and not HEADER_RE.match(line)
                    and (_starts_with_citation(line) or _cites(line))):
                # Run 25's claims round wrote eight findings as paragraphs -- "The guard checks two
                # conditions: `OFF_SWITCH.exists() and ...` at line 323 ... I verified by running
                # the hook directly: `touch /tmp/cc-guard-off` is denied, ..." -- and every one was
                # reported as citing nothing, because a citation only counted when it opened the
                # line. All eight had done the work and said where. The claim stays claimed, so a
                # second sentence of the same paragraph is read too. A line that already carries
                # a header is left alone: prefixing EVIDENCE to `EVIDENCE: command: rg -n 'a:1-2'`
                # made the header part of the body, and the body then read as a file citation.
                out.append("EVIDENCE: %s" % line.strip())
            else:
                head = HEADER_RE.match(line)
                said = _SAID_EVIDENCE.search(line, head.end() if head else 0)
                if said and _cites(line[said.end():].strip()):
                    # `... becomes a no-op for the session. Evidence: line 280-281 of `guard.py`.`
                    # The word at the end of the paragraph rather than the start of a line, which is
                    # where a paragraph puts it. Six claims cited nothing with the citation sitting in
                    # the last sentence of each. Under a CLAIM header the same sentence carries the
                    # citation of the finding it states, so the header keeps its sentence and loses
                    # the citation to a line of its own.
                    out.append(line[:said.start()].rstrip() if head else line)
                    out.append("EVIDENCE: %s" % line[said.end():].strip())
                    claimed = False
                else:
                    out.append(line)
                    if head:
                        # A plain `CLAIM:` line, with no emphasis to strip, never reached the branch
                        # below that notes a claim has been stated -- only an emphasised one did. So
                        # the sentences under the plainest possible header, the one the contract asks
                        # for, were the only ones never read as its evidence.
                        claimed = head.group("name").upper() == "CLAIM"
                    else:
                        claimed = claimed and not line.strip()
            continue
        mark = _EMPHASIS.search(seen.group("lead"))
        body, extra = seen.group("body"), ""
        if mark:
            closing = body.find(mark.group(1))
            if closing != -1:
                body, extra = body[:closing], body[closing + len(mark.group(1)):]
            if not body.strip() and extra.strip():
                # `**CLAIM 1:** the rule is broader than its intent.` -- the emphasis closing on the
                # colon rather than after the sentence, which is how anyone writing markdown would
                # bold a heading. Split at the mark, the claim is the empty string and the sentence
                # is trailing prose, so it was dropped: eight findings arrived as `claim 1 ()` and
                # were refused for citing nothing, the citations having gone the same way.
                body, extra = extra, ""
        # `CLAIM: the rule is narrow. Evidence: g.py:12-14` -- the citation in the same sentence as
        # the claim, which is where it lands when the heading said the word once and the finding is a
        # paragraph. Left in the claim it is not evidence of anything and the claim reads oddly.
        inline = ""
        said = _SAID_EVIDENCE.search(body)
        if said and _cites(body[said.end():].strip()):
            inline = body[said.end():].strip()
            body = body[:said.start()].strip()
        out.append("%s: %s" % (seen.group("name").upper(), body.strip()))
        claimed = seen.group("name").upper() == "CLAIM"
        if inline:
            out.append("EVIDENCE: %s" % inline)
            claimed = False
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
# `\b` and `\f` are escapes in JSON and word-boundaries in a regex, and this repo's code is mostly
# regex. Decoding them turned a correct quote of `_VERBS = r"(?:touch|mv|...)\b[^;|&\n]*"` into a
# line carrying a backspace, which is in no file. The pair is protected across the decode and put
# back as the two characters that were written -- unlike `\n`, which a model does mean as a line
# break when it hands over a multi-line quote. Only an even number of backslashes counts, so an
# intentional `\\b` still decodes to a literal one.
_REGEXY = re.compile(r"(?<!\\)((?:\\\\)*)\\([bf])")
# A private-use character, not a control one: JSON refuses a raw NUL in a string, so the decode this
# was meant to protect failed outright and the fallback path did the work instead -- silently, and
# with the newline it was there to prevent.
_KEPT = "\ue000"


def _protect(text: str) -> str:
    return _REGEXY.sub(lambda m: m.group(1) + _KEPT + m.group(2), text)


def _restore(text: str) -> str:
    for letter in "bfn":
        text = text.replace(_KEPT + letter, "\\" + letter)
    return text


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
                text, _ = json.JSONDecoder().raw_decode(_protect(candidate))
            except ValueError:
                continue
            return found, _restore(text)
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


# `"...text..." (line 229); "...more..." (line 246)` -- several quotes on one QUOTES line, each
# followed by the line it came from. The split is only made where a citation's closing bracket meets
# the next opening quote: the code being quoted is regex and shell, full of semicolons of its own,
# and splitting on those tore three good quotes into six broken ones.
_BETWEEN = re.compile(r"(?<=\))\s*[;,]\s*(?=[\"'`])")

# What a stage writes when it has run something and wants to say so: the command, then what it did.
# Nothing here is taken on the strength of the sentence -- the command still has to appear in the
# transcript and the output still has to bear the description out.
# `produced` before `output`, because a probe reported as `... produced no output (ALLOW)` splits at
# whichever word comes first in the line: at `output` the command keeps `produced no` and what it
# printed is read as `(ALLOW)`, and the probe fails against its own recording.
_DID = re.compile(r"\s+(?:->|returns|returned|produced|produces|prints|printed|said|says|"
                  r"outputs|output|gives|gave|yielded|emitted)\s+")


_AFTERWARDS = re.compile(r"\s*\((?:[Ll]ines?\s+)(?P<start>\d{1,6})"
                         r"(?:\s*[-\u2013]\s*(?P<end>\d{1,6}))?\)\s*$")


# `Ran four hooks. `touch /tmp/cc-guard-off` -- denied. `rm /tmp/cc-guard-off` -- allowed.` A stage
# that has run four probes and is reporting them one to a sentence. Every one is checkable -- the
# command has to be in the transcript and the outcome has to be borne out by what it printed -- and
# every one was read as prose, so eight findings established by running the hook cited nothing.
_PROBED = re.compile(r"`(?P<command>[^`]{4,400})`\s*(?:--|\u2014|->|:|,)?\s*"
                     r"(?P<outcome>[^.;`]{2,120})")
_LIKE_A_COMMAND = re.compile(r"^(?:\./)?[\w/~$]")
# `EVIDENCE: python3 scripts/guard_test.py -- "expected deny got deny"` -- the command with neither
# the header word nor backticks, which is how a stage writes it when it has just run the thing. Named
# programs only: an EVIDENCE line that opens on a sentence is a sentence.
_OPENS_ON_A_PROGRAM = re.compile(r"^(?:[A-Z_][A-Z0-9_]*=\S*\s+)*"
                                 r"(?:python3?|pytest|bash|sh|zsh|echo|printf|cat|grep|rg|git|make|"
                                 r"node|npm|uv|touch|mv|cp|rm|unlink|dd|tee|install|ln|shred|truncate|chmod|"
                                 r"mkdir|rmdir|ls|find|sed|awk|curl|jq|\./\S+|\S+\.(?:py|sh|js))\b")


def _bare_command(body: str) -> list[dict]:
    """A command run and reported without a header word or backticks to mark it."""
    if not _OPENS_ON_A_PROGRAM.match(body.strip()):
        return []
    command, expect = _ran(body.strip())
    if expect is None:
        parts = re.split(r"\s+--\s+", body.strip(), maxsplit=1)
        if len(parts) != 2:
            return []
        command, expect = parts[0], parts[1]
    return [{"kind": "command_result", "command": command.strip(),
             "expect": expect.strip().rstrip(".")}]
# `denied (line 307-310)` -- the outcome and, in the same breath, where in the file the denial comes
# from. The bracket is a citation, not part of what the command printed, and comparing it against the
# output failed a probe that had run and said exactly what the stage reported.
_OUTCOME_ASIDE = re.compile(r"\s*\([^)]*\)\s*$")


# What tells a quotation of code from a command line: an assignment, a comparison, a keyword joining
# two expressions, a call on an attribute. A shell command can contain any of these inside quotes,
# which is why a leading program name wins over this.
# The assignment must be spaced. `dd if=/dev/null of=/tmp/cc-guard-off` is a command with two
# arguments, and an unspaced `=` read it as code and dropped a probe that had actually run.
_READS_AS_CODE = re.compile(r"(?:==|!=|\bor\b|\band\b|\bnot\b|\bre\.|\bself\.|\w+ = [^=]"
                            r"|\w+\.\w+\()")


def _probes(body: str) -> list[dict]:
    """The commands a stage reported running in prose, with what it says each one did."""
    out: list[dict] = []
    for found in _PROBED.finditer(body):
        command = found.group("command").strip()
        outcome = _OUTCOME_ASIDE.sub("", found.group("outcome")).strip(" -\u2014:,")
        if not outcome or "/" not in command and " " not in command:
            # A backticked identifier with a word after it is prose about the code, not a command
            # somebody ran. `tampers()` is not a probe and neither is `rm`.
            continue
        if not _OPENS_ON_A_PROGRAM.match(command):
            # A command names a program. Without that the backtick scan reports the prose between two
            # snippets as something the stage ran: run 25's round produced `r"(?:[^\s\'\";|&]*/)?`,
            # `flag does NOT re`, `is denied but` and `function is only` as commands nobody could
            # find, and each phantom refuses the round as surely as a fabrication would. A whitelist
            # loses the odd real probe, which the stage is then told cites nothing and can restate --
            # recoverable, where a phantom is not.
            continue
        if _READS_AS_CODE.search(command) and not _OPENS_ON_A_PROGRAM.match(command):
            # `OFF_SWITCH.exists() and os.environ.get("CC_GUARD_LIFTABLE") == "1"` at line 323 is a
            # quotation of the file, and reading it as a command run by the stage turned the one
            # citation in the paragraph into an unverifiable one. A command names a program first;
            # this names a value and does something to it.
            continue
        if not _LIKE_A_COMMAND.match(command):
            # Backticks alternate, so a scan that pairs them wrongly reads the prose between two
            # snippets as a command: `OFF_SWITCH`. Only checks `DEPTH_OFF` yielded ". Only checks"
            # as something the stage had run, and four such phantoms were reported as unverified
            # commands against a stage that had cited nothing of the kind.
            continue
        out.append({"kind": "command_result", "command": command, "expect": outcome})
    return out


# `(From scripts/guard.py, lines 280-281)` on the line after the quote -- the stage saying where it
# copied from, which is the citation, put where a person puts an attribution. Swallowed into the
# quoted text it made the quote absent from the file it names.
_PROVENANCE = re.compile(r"^\(\s*(?:from|source|in|see)\s+(?P<rest>[^)]+)\)\s*[.;]?\s*$", re.I)


def _attributed(body: str) -> tuple[str, dict | None]:
    """Split a trailing attribution line off a quote, and read it as the citation it is."""
    lines = body.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return body, None
    seen = _PROVENANCE.match(lines[-1].strip())
    if not seen:
        return body, None
    found = _classify_all(seen.group("rest").replace(",", " "))
    return "\n".join(lines[:-1]), found[0] if found else None


def _spread(body: str) -> list[tuple[str, dict | None]]:
    """Split a QUOTES body into its quotes, each with the citation written after it.

    The quote runs from the first delimiter to the last, not to the next one: what is being quoted
    here is regex and shell, and `"_VERBS = r"(?:touch|mv)\b""` read up to the second delimiter is
    `_VERBS = r`, which matches nothing and reports a verbatim quote as absent. The line number
    after it says where the quote ends, so there is no ambiguity to resolve.
    """
    out: list[tuple[str, dict | None]] = []
    for piece in _BETWEEN.split(body):
        piece = piece.strip()
        if not piece:
            continue
        citation = None
        where = _AFTERWARDS.search(piece)
        if where:
            citation = {"kind": "file_quote", "path": None,
                        "start": int(where.group("start")),
                        "end": int(where.group("end") or where.group("start"))}
            piece = piece[:where.start()].strip()
        else:
            found = _classify_all(piece)
            if found and found[0].get("start"):
                citation = found[0]
        if len(piece) > 1 and piece[0] in "\"'`" and piece[-1] == piece[0]:
            piece = piece[1:-1]
        elif _first_span(piece) is not None:
            piece = _first_span(piece)
        else:
            return []
        out.append((_unescaped(piece), citation))
    return out


def _ran(body: str) -> tuple[str, str | None]:
    """Split a RUN line into the command and what the stage says it did."""
    parts = _DID.split(body, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip().rstrip(".")
    return body.strip(), None


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
        body, attributed = _attributed(body)
        if attributed and evidence is not None:
            for key, value in attributed.items():
                if key != "quote" and not evidence.get(key):
                    evidence[key] = value
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
        if evidence is not None and not evidence.get("start"):
            # `QUOTE: Line 246: `name = ...`` -- the label in front of the code rather than after it.
            # The label was stripped for the comparison and never read as the citation, so a ledger
            # of nine quoted lines, each with its own line number, was refused nine times for
            # missing a file and a line range.
            labelled = [_LABEL.match(l) for l in body.split("\n") if l.strip()]
            if labelled and all(labelled):
                numbers = [int(m.group("n")) for m in labelled]
                evidence["start"], evidence["end"] = min(numbers), max(numbers)
                evidence.setdefault("kind", "file_quote")
                body = "\n".join(m.group("code") for m in labelled)
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
        header = seen.group("name").upper() + ":" if seen else None
        if header == "QUOTES:":
            header = "QUOTE:"
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
                # A sentence can carry both kinds at once -- "the pattern `X` at line 267; `touch
                # /tmp/y` is denied" -- and taking only the first left the rest of the paragraph
                # uncited. Order matters only in that a quotation of the file is looked for first,
                # since source read as a command cannot be checked at all.
                found = (_quoted_at_line(body) + _probes(body)) or _bare_command(body) \
                    or [{"kind": None, "raw": body}]
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
            spread = _spread(rest)
            if len(spread) > 1:
                if evidence is not None and not evidence.get("quote") and len(evidence) <= 1:
                    # The branch above made an empty piece for a quote that had not arrived yet.
                    # These several have, so it is not one of them.
                    current["evidence"].remove(evidence)
                # Several quotes on one header line, each with its own line number after it. Split
                # only where a citation's closing bracket meets the next opening quote, because the
                # code being quoted is full of semicolons of its own.
                for text_, citation in spread:
                    piece = dict(citation or {})
                    piece.setdefault("kind", "file_quote")
                    piece["quote"] = text_
                    if piece.get("path") is None:
                        # The same resolution a quote under its own header gets: the claim's file if
                        # it named one, otherwise the tree, which answers only when exactly one file
                        # holds that text at those lines.
                        piece["path"] = claim_path
                    if piece.get("path") is None and piece.get("start"):
                        piece["path"] = resolve_path(root, piece["start"],
                                                     piece.get("end") or piece["start"], text_)
                    current["evidence"].append(piece)
                evidence = current["evidence"][-1]
                cited = [evidence]
                quote = None
            elif rest:
                quote.append(rest)
        elif header == "RUN:":
            body = line[seen.end():].strip()
            command, expect = _ran(body)
            evidence = {"kind": "command_result", "command": command, "expect": expect}
            current["evidence"].append(evidence)
            cited = [evidence]
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

    # Anything still holding lines and text but no file, whichever header shape it arrived in.
    # The resolution was written three times, once per branch, and the fourth shape was refused for
    # citing nothing while the two beside it resolved. Doing it once at the end is the same rule for
    # every spelling, and it stays strict: the tree answers only when exactly one file holds that
    # text at those lines.
    if root:
        for claim in claims:
            for piece in claim["evidence"]:
                if piece.get("quote") and piece.get("start") and not piece.get("path"):
                    where = resolve_path(root, piece["start"],
                                         piece.get("end") or piece["start"], piece["quote"])
                    if where:
                        piece["path"] = where
                        piece["kind"] = "file_quote"

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
    if len(hits) == 1:
        return hits[0]
    if hits:
        return None
    # Nothing matched cleanly. Finding the address is not the same as approving the quote, and a
    # near-miss still has an address: a quote that dedented its first line and kept the second is
    # refused either way, but "cites nothing" sends a stage looking for a citation it already wrote,
    # while "reindented by -4" is a sentence it can act on. Only retouching is looked for, and still
    # only when one file answers.
    near = [path for path in _tracked(root)
            if file_quote(root, path, start, end, quote).kind in (RETOUCHED, WRONG_LINES)]
    return near[0] if len(near) == 1 else None


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
