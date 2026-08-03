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
ACCEPTABLE = {PASS, INDENT_DRIFT}


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
            for line in _unlabelled(_ungutted(_unfenced(text), first), first)
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

    if [l.strip() for l in cited] == [l.strip() for l in quoted]:
        for n, (was, now) in enumerate(zip(cited, quoted)):
            if was != now:
                return Verdict(RETOUCHED, "%s:%d-%d, line %d whitespace altered (%d spaces in the "
                                          "file, %d in the quote)"
                               % (path, start, end, start + n, _indent(was), _indent(now)))

    # Right text, wrong address: worth distinguishing, because it means the model read the file and
    # mis-remembered where, rather than inventing the content.
    haystack = _significant("\n".join(lines))
    needle = _dedented(quoted)
    for i in range(len(haystack) - len(needle) + 1):
        if _dedented(haystack[i:i + len(needle)]) == needle:
            return Verdict(WRONG_LINES, "cited %s:%d-%d, text is near line %d"
                           % (path, start, end, i + 1))
    return Verdict(FAIL, "quote not present in %s" % path)


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
HEADERS = ("CLAIM:", "EVIDENCE:", "QUOTE:", "UNKNOWN:", "SEVERITY:", "FALSIFICATION:",
           "PREDICT:")

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


def parse_ledger(text: str, root: str = "") -> tuple[list[dict], list[str]]:
    """Split an answer into claims and declared unknowns.

    Scanned line by line rather than matched with one regex, because a QUOTE runs until the next
    header and quoted code contains blank lines, colons and anything else a regex would trip on.
    The rule is simply: a quote ends at the next line beginning with a known header, or at the end.
    """
    claims: list[dict] = []
    current: dict | None = None
    evidence: dict | None = None
    cited: list[dict] = []
    quote: list[str] | None = None

    def close_quote() -> None:
        nonlocal quote, evidence
        if quote is None:
            quote = None
            return
        body = "\n".join(quote).strip("\n")
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
        header = next((h for h in HEADERS if line.startswith(h)), None)
        if quote is not None and header is None:
            quote.append(line)
            continue
        close_quote()
        if header == "CLAIM:":
            current = {"claim": line[len("CLAIM:"):].strip(), "evidence": [],
                       "severity": None, "falsification": None}
            claims.append(current)
            evidence = None
        elif current is None:
            continue
        elif header == "EVIDENCE:":
            body = line[len("EVIDENCE:"):].strip()
            found = _classify_all(body)
            if not found:
                found = [{"kind": None, "raw": body}]
            current["evidence"].extend(found)
            cited = found
            # A QUOTE that follows attaches to the last citation named, which is the one it is
            # under. The earlier ones are still checked, on their line numbers alone.
            evidence = found[-1]
        elif header == "QUOTE:":
            quote = []
            rest = line[len("QUOTE:"):].strip()
            if rest:
                quote.append(rest)
        elif header == "SEVERITY:":
            current["severity"] = line[len("SEVERITY:"):].strip().lower()
        elif header == "FALSIFICATION:":
            current["falsification"] = {"command": line[len("FALSIFICATION:"):].strip()}
    close_quote()

    for claim in claims:
        for piece in claim["evidence"]:
            if piece.get("kind") is not None or not root or not piece.get("quote"):
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
    return claims, [m.group("unknown") for m in UNKNOWN_RE.finditer(text)]


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
