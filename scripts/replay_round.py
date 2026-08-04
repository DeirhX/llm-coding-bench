#!/usr/bin/env python3
"""Re-judge a round that has already been judged, with today's verifier.

Every fix to the gate is a guess about a real round until it is run against one. The rounds are
kept -- a stage's answer and the tree it was written against both survive in the run directory --
so a change can be measured on the refusal it was meant to lift instead of on a test written from
memory of it.

    python3 scripts/replay_round.py /tmp/r26tree/artifacts/depth/<session>/flow.json --tree /tmp/r26tree

Prints, per claim, what the gate says now and what it said then. The two columns are the whole
point: a fix that turns a sound claim from refused to verified is working, and one that turns a
fabricated claim the same way has broken the gate.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cc_evidence  # noqa: E402
import cc_ledger  # noqa: E402
import cc_verify  # noqa: E402


def rounds(state: dict) -> list[dict]:
    return [e for e in state.get("stages", []) if e.get("answer")]


def calls_of(transcript: str, agent: str) -> list:
    """Tool calls the stage actually made, so command evidence is judged against what ran."""
    if not transcript or not os.path.isfile(transcript):
        return []
    try:
        return cc_evidence.collect(transcript)
    except Exception as bad:  # a transcript half-written when the run was killed
        print("  (transcript unreadable: %s)" % bad)
        return []


def judge(entry: dict, tree: str, transcript: str) -> None:
    answer = entry.get("answer") or ""
    claims, _ = cc_ledger.claims_from_text(answer, tree)
    calls = calls_of(transcript, entry.get("agent") or "")
    print("== %s%s: %d claims, %d gaps then, %d tool calls on record"
          % (entry.get("stage"), " (reopened)" if entry.get("reopened") else "",
             len(claims), len(entry.get("gaps") or []), len(calls)))
    for n, claim in enumerate(claims, 1):
        head = " ".join((claim.claim or "").split())[:64]
        if not claim.evidence:
            print("  %2d. CITES NOTHING  %s" % (n, head))
            continue
        for cited in claim.evidence:
            if cited.kind == cc_ledger.COMMAND_RESULT:
                said = cc_verify.command_result(calls, cited.command or "", cited.expect or "")
                what = "command %r" % ((cited.command or "")[:44])
            else:
                said = cc_verify.file_quote(tree, cited.path or "", cited.start, cited.end,
                                            cited.quote or "")
                what = "%s:%s-%s" % (cited.path, cited.start, cited.end)
            print("  %2d. %-11s %-58s %s" % (n, said.kind, what, head if cited is claim.evidence[0] else ""))
            if said.kind != cc_verify.PASS:
                print("      %s" % " ".join((said.detail or "").split())[:150])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("flow", help="the flow.json of a finished or killed run")
    ap.add_argument("--tree", required=True, help="the tree the stage read")
    ap.add_argument("--transcript", default="", help="the stage's jsonl, for command evidence")
    args = ap.parse_args()
    state = json.load(open(args.flow))
    found = rounds(state)
    if not found:
        print("no round in this run wrote an answer")
        return 1
    for entry in found:
        judge(entry, args.tree, args.transcript)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
