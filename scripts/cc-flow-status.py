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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--session", default=os.environ.get("CLAUDE_SESSION_ID", ""))
    ap.add_argument("--root", default=os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))
    ap.add_argument("--short", action="store_true", help="one line, for a status bar")
    args = ap.parse_args()

    if not args.session:
        print("no session id, so no flow to report on")
        return 0

    state = cc_flowstate.load(args.session, args.root)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
