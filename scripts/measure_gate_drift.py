#!/usr/bin/env python3
"""Re-judge every answer the gate has already judged, and report what changed.

Every rule in this gate was added from a single observed failure, and a rule added that way is
measured against the answer that motivated it and nothing else. The cost nobody sees is the other
direction: an answer that was fine and is now refused, which looks identical in the artifacts to one
that needed correcting, because both end in a refusal round the model then satisfies.

So: replay each recorded stage against the current gate, with its original transcript, and bucket
the gaps by cause. The buckets matter because this repository reviews itself -- the files those runs
cite are the files this work edits, so a citation that no longer matches is drift in the tree rather
than a change in judgement, and counting the two together would report a false-refusal rate made
mostly of my own commits.

Usage: measure_gate_drift.py [--artifacts DIR] [--verbose]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import cc_evidence  # noqa: E402
import cc_ledger  # noqa: E402
import depth_pipeline as dp  # noqa: E402

# Which file each stage's answer went to, since a stage is named for what it does and its artifact
# for what it produced.
ANSWER = {"claims": "claims.md", "adversary": "verdict.md", "survey": "survey.md",
          "plan": "plan.md", "implement": "change.md", "verify": "verdict.md"}

# A gap is attributed by the sentence it opens with, which is the only stable handle the gate gives.
#
# Everything that compares a citation against the tree is drift, and `wrong-lines` most of all: it
# says the quoted text is still there and has moved, which is what inserting a function above it
# does. The first version of this script left it out and reported five rule-caused refusals where
# there was one -- a measurement that would have justified reverting rules that were working.
DRIFT = ("fail --", "retouched", "wrong-lines", "no read in this session covered", "uncovered")
MALFORMED = ("incomplete file_quote", "unknown evidence kind", "cites nothing")
NEW_RULES = ("failed and then passed", "did not exist yet", "did not print what the plan said",
             "commits to nothing", "asserts only that a mock was called",
             "swallows the exception", "is never passed by any caller")


def bucket(gap: str) -> str:
    lowered = gap.lower()
    if any(k in lowered for k in NEW_RULES):
        return "new rule"
    if any(k in lowered for k in DRIFT):
        return "tree drift"
    if any(k in lowered for k in MALFORMED):
        return "malformed citation"
    return "older rule"


def judge(run: Path, verbose: bool) -> list[dict]:
    """Every verified stage of one run, as it was judged then and as it would be judged now."""
    meta = json.loads((run / "run.json").read_text())
    contract = cc_ledger.contract_for(meta.get("adapter") or "review")
    # The tree the run read. Only the runs against this repository can be replayed: the throwaway
    # worktrees the others used are gone, and judging citations against a missing tree would report
    # every one of them as a fabrication.
    root = REPO if run.name == "self" else None
    out = []
    for stage in meta["stages"]:
        recorded = run / ("%s.gate.json" % stage["stage"])
        answer = run / ANSWER.get(stage["stage"], "")
        if not recorded.is_file() or not answer.is_file():
            continue
        row = {"run": run.parent.name, "side": run.name, "stage": stage["stage"],
               "was": len(json.loads(recorded.read_text()).get("gaps", []))}
        if root is None:
            row["now"] = None
            out.append(row)
            continue
        claims, unknowns = cc_ledger.claims_from_text(answer.read_text(errors="replace"))
        transcript = dp.transcript_for(stage["session"], str(root))
        calls = cc_evidence.collect(str(transcript)) if transcript.is_file() else []
        gaps, _ = dp.load_gate().evaluate(contract, claims, unknowns, calls, str(root),
                                          check_coverage=transcript.is_file(),
                                          answer=answer.read_text(errors="replace"))
        row["now"] = len(gaps)
        row["buckets"] = {}
        for gap in gaps:
            row["buckets"][bucket(gap)] = row["buckets"].get(bucket(gap), 0) + 1
        if verbose:
            row["gaps"] = [g[:160] for g in gaps]
        out.append(row)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--artifacts", default=str(REPO / "artifacts/reviews"))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    rows = []
    for run in sorted(Path(args.artifacts).glob("*/*/run.json")):
        rows += judge(run.parent, args.verbose)

    replayable = [r for r in rows if r["now"] is not None]
    accepted = [r for r in replayable if r["was"] == 0]
    now_refused = [r for r in accepted if r["now"] > 0]
    only_drift = [r for r in now_refused
                  if set(r.get("buckets", {})) <= {"tree drift", "malformed citation"}]

    print("%d stage(s) on disk, %d replayable against a tree that still exists"
          % (len(rows), len(replayable)))
    print("%d were accepted then; %d of those the current gate refuses"
          % (len(accepted), len(now_refused)))
    print("  of which %d only because the files they cite have been edited since"
          % len(only_drift))
    rules = [r for r in now_refused if set(r.get("buckets", {})) - {"tree drift",
                                                                    "malformed citation"}]
    print("  leaving %d refused by a rule that did not exist when they were accepted"
          % len(rules))
    print()
    for row in rows:
        now = "n/a" if row["now"] is None else str(row["now"])
        print("%-16s %-6s %-10s was %d, now %s  %s"
              % (row["run"], row["side"], row["stage"], row["was"], now,
                 row.get("buckets", "") or ""))
        for gap in row.get("gaps", []):
            print("        %s" % gap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
