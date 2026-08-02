#!/usr/bin/env python3
"""Run the depth gate against real answers this project has already produced, good and bad.

A gate that has only ever seen synthetic input is a gate whose thresholds were chosen by the person
who wrote it. These two fixtures were produced by the 31B before the gate existed, so neither was
written to pass or fail it:

* **negative** -- the refactor proposal that this whole design exists to reject. It proposed a
  typed `AgentResult` and a central `Config` on the strength of `grep -c os.environ.get`, having
  read no caller and run nothing that could fail. If the gate does not stop this, it is decoration.
* **positive** -- the five findings from the depth-gate compliance spike, four of which are real
  defects in this repository. The gate must not cost them. A gate that blocks everything is as
  useless as one that blocks nothing, and only the second failure is embarrassing enough to notice.

The transcripts are pruned copies, checked in beside the fixtures. Pruning keeps the tool calls,
their line coverage and their output while dropping file contents, which makes the fixture a few KB
instead of a few MB and keeps the check reproducible after `~/.claude` is cleaned out.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "benches/depth/fixtures"
sys.path.insert(0, str(REPO / "scripts"))

import cc_evidence  # noqa: E402
import cc_ledger  # noqa: E402

# Where each fixture came from, so a future reader can go back to the source session.
SOURCES = {
    "negative": "f2111019-2095-4e45-bfd0-233cca07373e",
    "positive": "34d843e5-0487-4868-9027-9bff27667030",
}
LIVE = Path.home() / ".claude/projects/-Users-deirh-Projects-llm-coding-bench"


def prune(src: Path, dst: Path) -> int:
    """Keep what the recorder reads; drop the file bodies, which are the bulk."""
    kept = []
    for event in cc_evidence.iter_events(str(src)):
        message = event.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        blocks = [b for b in content if isinstance(b, dict)
                  and b.get("type") in ("tool_use", "tool_result")]
        if not blocks:
            continue
        for b in blocks:
            if b.get("type") == "tool_result" and isinstance(b.get("content"), str):
                b["content"] = b["content"][:4000]
        detail = event.get("toolUseResult")
        if isinstance(detail, dict) and isinstance(detail.get("file"), dict):
            detail["file"].pop("content", None)
        elif isinstance(detail, str):
            detail = detail[:4000]
        kept.append({"type": event.get("type"), "timestamp": event.get("timestamp"),
                     "toolUseResult": detail,
                     "message": {"role": message.get("role"), "content": blocks}})
    dst.write_text("\n".join(json.dumps(e) for e in kept) + "\n")
    return len(kept)


def refresh() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, session in SOURCES.items():
        src = LIVE / ("%s.jsonl" % session)
        if not src.is_file():
            print("skipped %s: no live transcript at %s" % (name, src), file=sys.stderr)
            continue
        n = prune(src, FIXTURES / ("%s.transcript.jsonl" % name))
        print("%-9s %d tool events from %s" % (name, n, session))
    return 0


def run_one(name: str, adapter: str, answer_file: Path) -> dict:
    import importlib.util
    spec = importlib.util.spec_from_file_location("depth_gate", REPO / "scripts/cc-depth-gate.py")
    gate = importlib.util.module_from_spec(spec)
    sys.modules["depth_gate"] = gate
    spec.loader.exec_module(gate)

    answer = answer_file.read_text()
    transcript = FIXTURES / ("%s.transcript.jsonl" % name)
    calls = cc_evidence.collect(str(transcript)) if transcript.is_file() else []
    claims, unknowns = cc_ledger.claims_from_text(answer)
    contract = cc_ledger.contract_for(adapter)
    gaps, report = gate.evaluate(contract, claims, unknowns, calls, str(REPO),
                                 check_coverage=transcript.is_file())
    accepted = [v for v in report["verdicts"] if v.get("verdict") in ("pass", "indent-drift")]
    # A claim called `fail`, `uncovered` or `wrong-lines` has been called a fabrication. A claim
    # called `retouched` has not: its content is right and its whitespace was tidied while copying,
    # so the finding stands and only the quote must be re-taken. Conflating the two would let the
    # gate report a real defect as an invention, which is the error it exists to prevent.
    rejected = [v for v in report["verdicts"]
                if v.get("verdict") in ("fail", "uncovered", "wrong-lines", "no-evidence")]
    return {"fixture": name, "adapter": adapter, "claims": len(claims),
            "accepted": len(accepted), "rejected": len(rejected), "gaps": gaps,
            "unknowns": unknowns, "probes": report["probes_run"], "blocked": bool(gaps),
            "verdicts": [v.get("verdict") for v in report["verdicts"]]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true", help="re-prune from ~/.claude and exit")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.refresh:
        return refresh()

    cases = [
        ("negative", "refactor-proposal", "negative_agentresult.md"),
        ("negative", "refactor-proposal", "negative_agentresult_steelman.md"),
        ("positive", "review", "positive_spike_findings.md"),
    ]
    results = []
    for name, adapter, filename in cases:
        path = FIXTURES / filename
        if not path.is_file():
            print("missing fixture %s" % path, file=sys.stderr)
            continue
        r = run_one(name, adapter, path)
        r["file"] = filename
        results.append(r)

    if args.json:
        json.dump(results, sys.stdout, indent=2)
        print()
        return 0

    failures = 0
    for r in results:
        verdict = "BLOCKED" if r["blocked"] else "allowed"
        print("%-38s %-18s %s  %d claim(s), %d accepted, %d probe(s)"
              % (r["file"], r["adapter"], verdict, r["claims"], r["accepted"], r["probes"]))
        for g in r["gaps"]:
            print("    - %s" % g[:150])
        if r["fixture"] == "negative" and not r["blocked"]:
            failures += 1
            print("    !! this is the argument the gate exists to refuse, and it passed")
        if r["fixture"] == "positive":
            if r["rejected"]:
                failures += 1
                print("    !! %d real finding(s) called fabricated" % r["rejected"])
            if r["accepted"] < 4:
                failures += 1
                print("    !! only %d of the real findings survived" % r["accepted"])
            if r["blocked"]:
                # Not a failure: the findings all stand, and one quote has to be taken again.
                print("    (blocked on quote fidelity only -- the cost is one extra round)")
    print("\n%d fixture(s), %d unexpected" % (len(results), failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
