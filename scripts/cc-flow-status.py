#!/usr/bin/env python3
"""What the running flow has done so far, for a person watching or for the status line.

The client shows a subagent working, which answers "is something happening" and not "is it getting
anywhere". This answers the second: which stages have passed, which was refused and on what grounds,
and what runs next.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cc_flowstate     # noqa: E402


def _wrapped(text: str, width: int = 96, indent: str = " " * 6) -> str:
    """One paragraph, folded, because a gap is a sentence and a truncated sentence is not one."""
    words, lines, line = " ".join((text or "").split()).split(), [], ""
    for word in words:
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = "%s %s" % (line, word) if line else word
    if line:
        lines.append(line)
    return "\n".join(indent + l for l in lines)


def findings(state: dict) -> str:
    """What the run established and what it was refused for, round by round.

    flow.json holds all of this and nothing read it out: every post-mortem this month began by
    writing the same twenty lines of Python at a shell. A stage that was given up on has usually
    proved something before it was, and that is exactly the part a person wants and the summary
    line does not carry.
    """
    out = []
    for entry in state.get("stages", []):
        stood, gaps = entry.get("stood") or [], entry.get("gaps") or []
        out.append("%s: %s, %s tool calls" % (entry.get("stage"), entry.get("verdict") or "running",
                                              entry.get("calls", 0)))
        for finding in stood:
            out.append("    proved: %s" % " ".join((finding.get("claim") or "").split()))
            for cite in finding.get("cites") or []:
                out.append("            %s" % " ".join(str(cite).split()))
        for gap in gaps:
            out.append("    refused:")
            out.append(_wrapped(gap))
        if not stood and not gaps:
            out.append("    nothing recorded against this round")
    return "\n".join(out) or "no stage has reported"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--session", default=os.environ.get("CLAUDE_SESSION_ID", ""))
    ap.add_argument("--root", default=os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    ap.add_argument("--short", action="store_true", help="one line, for a status bar")
    ap.add_argument("--findings", action="store_true",
                    help="every finding that carried its evidence, and every gap that refused one")
    args = ap.parse_args()

    if not args.session:
        print("no session id, so no flow to report on")
        return 0

    state = cc_flowstate.peek(args.session, args.root)
    if not state.get("flow"):
        print("no flow running")
        return 0

    if args.short:
        done = cc_flowstate.done(state)
        left = cc_flowstate.next_stage(state)
        bad = cc_flowstate.refused(state)
        print("%s %d done%s%s" % (state["flow"], len(done),
                                  ", %s refused" % bad[-1]["stage"] if bad else "",
                                  ", next %s" % left if left else ", complete"))
        return 0

    print(cc_flowstate.summary(state))
    if args.findings:
        print()
        print(findings(state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
