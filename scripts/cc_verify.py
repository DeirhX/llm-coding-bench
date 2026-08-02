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


def _significant(text: str) -> list[str]:
    """Lines that carry content, with trailing whitespace, blank lines and code fences discarded."""
    return [line.rstrip() for line in _unfenced(text).strip("\n").split("\n") if line.strip()]


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
    if len(lines) >= 2 and lines[0].lstrip().startswith("```") and lines[-1].strip() == "```":
        lines = lines[1:-1]
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
    quoted = _significant(quote)
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
HEADERS = ("CLAIM:", "EVIDENCE:", "QUOTE:", "UNKNOWN:", "SEVERITY:", "FALSIFICATION:")

# The four shapes an EVIDENCE line may take. Only the first was accepted originally, which quietly
# made two adapters impossible to satisfy: refactor-proposal requires an `absence` search and
# ops-perf a `log_match`, and a model writing blocks had nowhere to put either. The gate would then
# have demanded, every time, something the answer format could not express.
FILE_EV = re.compile(r"^(?P<path>\S+?):(?P<start>\d+)-(?P<end>\d+)$")
COMMAND_EV = re.compile(r"^command:\s*(?P<command>.+?)(?:\s*->\s*(?P<expect>.+?))?$", re.I)
ABSENCE_EV = re.compile(r"^absence:\s*(?P<pattern>.+?)(?:\s+in\s+(?P<globs>\S+))?$", re.I)
LOG_EV = re.compile(r"^log:\s*(?P<path>\S+)\s*~\s*(?P<pattern>.+)$", re.I)


def _classify(body: str) -> dict | None:
    m = FILE_EV.match(body)
    if m:
        return {"kind": "file_quote", "path": m.group("path"),
                "start": int(m.group("start")), "end": int(m.group("end"))}
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


def parse_ledger(text: str) -> tuple[list[dict], list[str]]:
    """Split an answer into claims and declared unknowns.

    Scanned line by line rather than matched with one regex, because a QUOTE runs until the next
    header and quoted code contains blank lines, colons and anything else a regex would trip on.
    The rule is simply: a quote ends at the next line beginning with a known header, or at the end.
    """
    claims: list[dict] = []
    current: dict | None = None
    evidence: dict | None = None
    quote: list[str] | None = None

    def close_quote() -> None:
        nonlocal quote, evidence
        if quote is not None and evidence is not None:
            evidence["quote"] = "\n".join(quote).strip("\n")
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
            evidence = _classify(line[len("EVIDENCE:"):].strip())
            if evidence is None:
                evidence = {"kind": None, "raw": line[len("EVIDENCE:"):].strip()}
            current["evidence"].append(evidence)
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

    # The single-file_quote view the verifier and its tests were written against.
    for claim in claims:
        first = next((e for e in claim["evidence"] if e.get("kind") == "file_quote"), {})
        claim.update({"path": first.get("path"), "start": first.get("start"),
                      "end": first.get("end"), "quote": first.get("quote")})
    return claims, [m.group("unknown") for m in UNKNOWN_RE.finditer(text)]


def verify_ledger(root: str, text: str) -> list[tuple[dict, Verdict]]:
    out = []
    for claim in parse_ledger(text)[0]:
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
