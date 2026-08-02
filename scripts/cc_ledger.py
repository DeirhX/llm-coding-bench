#!/usr/bin/env python3
"""The claim ledger: what an answer must show before the harness lets it finish.

The ledger is deliberately task-agnostic. The failure being fixed is premature closure -- the model
reaches a story that sounds complete and stops -- and that failure is not review-specific, so review
is one adapter here rather than the architecture. An adapter names which evidence kinds a task type
demands and how many probes it must run; the engine underneath never changes.

Two shapes are accepted for the ledger itself, and the second exists because of a measurement. The
canonical form is ``claims.jsonl`` written with the Write tool. But in the depth-gate spike the 31B,
refused once, produced perfectly-formed ``CLAIM:`` / ``EVIDENCE:`` / ``QUOTE:`` blocks in its reply
and made no attempt to write a file -- so the gate also parses the blocks out of the final message.
Insisting on the JSON would have failed a run that had in fact done the work.

Evidence kinds are few on purpose (see the plan): ``file_quote``, ``command_result``, ``log_match``,
``absence``. ``absence`` gets its own kind because "nothing else uses this key" is the most abused
claim shape in this project's history and the one a quote can never support.

``unknown`` is a first-class verdict. A legal way to say "did not check" is the antidote to
fabricated closure, so every adapter permits unknowns and the gate asks for them by name.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

ARTIFACT_ROOT = "artifacts/depth"

FILE_QUOTE = "file_quote"
COMMAND_RESULT = "command_result"
LOG_MATCH = "log_match"
ABSENCE = "absence"
EVIDENCE_KINDS = (FILE_QUOTE, COMMAND_RESULT, LOG_MATCH, ABSENCE)


@dataclass
class Contract:
    """What a given task type must produce before the gate will let it stop.

    Every field is something the gate can check by arithmetic. Anything that would need judgement
    belongs in the adapter's prose, not here.
    """

    adapter: str
    summary: str
    required_evidence: tuple[str, ...] = (FILE_QUOTE,)
    # Commands the session must actually have run -- not described, run. 0 means the task type is
    # legitimately read-only (a review of code that does not execute).
    min_probes: int = 0
    # Whether a claim naming no wrong behaviour is refused. True for review, where the task is
    # defects in code that exists; false where an opinion about a design is the point of the task.
    defects_only: bool = False
    # A high-severity claim asserts something expensive to be wrong about, so it must carry the
    # cheapest disproof that was actually attempted. In this project's history the only things that
    # ever caught real bugs were probes, never prose.
    high_severity_needs_falsification: bool = True
    # Keeps the refusal finite. A gate that lists forty gaps is a gate nobody reads, and at ~12
    # tok/s the reading is charged to the session.
    claim_cap: int = 25
    # Some task types are only meaningful as a comparison: an ops claim needs a before and an after.
    min_measurements: int = 0
    # Whether the session must show the same command failing and then passing. Only an adapter whose
    # task is to change behaviour can ask for this, and for those it is the whole point: a claim that
    # a change works is the one claim the code it changed cannot support.
    needs_red_green: bool = False
    notes: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


# The adapters. Thin by design: same engine, different required evidence and stance.
ADAPTERS: dict[str, Contract] = {
    "review": Contract(
        adapter="review",
        summary="Claims about defects in code that already exists.",
        required_evidence=(FILE_QUOTE,),
        min_probes=0,
        defects_only=True,
        high_severity_needs_falsification=True,
        # The third note is here because three consecutive runs produced the same non-finding: that
        # the proxy is "structurally coupled to Anthropic and OpenAI and will need duplication to
        # add a backend", quoted from a function signature. It names nothing the code does wrong,
        # and there is no reading of it under which anything is broken.
        notes=("Every defect claim quotes the code it is about.",
               "A high-severity defect needs a probe that reproduces it, or it is not high.",
               "A claim that names no wrong behaviour is not a defect. That a design would be "
               "awkward to extend, quoted from a signature, is an opinion; leave it out.",
               "No defect found is a legal and complete answer."),
    ),
    "debug": Contract(
        adapter="debug",
        summary="Claims about the cause of an observed failure.",
        required_evidence=(COMMAND_RESULT,),
        min_probes=2,
        notes=("One command must reproduce the failure.",
               "One command must show the proposed cause changes it.",
               "A cause you cannot make appear and disappear is a hypothesis; label it unknown."),
    ),
    "refactor-proposal": Contract(
        adapter="refactor-proposal",
        summary="Claims that code should be restructured.",
        required_evidence=(FILE_QUOTE, ABSENCE),
        min_probes=1,
        notes=("Every duplication or scattered-config claim enumerates its sites as file_quotes.",
               "Every 'nothing else depends on this' needs an absence search, not an impression.",
               "A proposal with no probe at all cannot pass: run something that would break."),
    ),
    "ops-perf": Contract(
        adapter="ops-perf",
        summary="Claims about runtime behaviour, throughput or cost.",
        required_evidence=(LOG_MATCH,),
        min_probes=1,
        min_measurements=2,
        notes=("Cite real log lines, not recalled ones.",
               "A performance claim needs a before and an after.",
               "Do not cite decode speed for a cost that prefill dominates."),
    ),
    "implement": Contract(
        adapter="implement",
        summary="Claims that a change was made and that it works.",
        # No file_quote. Every other adapter reasons about code someone else wrote, where a quote is
        # a fact about the world; here the session wrote the lines it would be quoting, so a quote
        # says only that it can read back its own diff. The evidence that a change works has to come
        # from something that did not take instructions from the model: a command's exit status.
        required_evidence=(COMMAND_RESULT,),
        # Three, and they are named: the failing test, the same test passing, and the suite that
        # says nothing else broke. Fewer cannot distinguish a fix from a deletion.
        min_probes=3,
        # The red/green pair is a falsification -- a stronger one than a sentence about a command,
        # since the gate reads both outcomes out of the transcript itself.
        high_severity_needs_falsification=False,
        needs_red_green=True,
        notes=("Run the test that fails before the change, and show what it printed.",
               "Run the same command after, unchanged, and show it passing.",
               "Run the suite around it and cite its counts; a fix that breaks nine others is not "
               "a fix.",
               "Quoting the code you just wrote proves that you wrote it and nothing else.",
               "Whatever you did not run is UNKNOWN, which is a legal answer here too."),
    ),
    "bench-audit": Contract(
        adapter="bench-audit",
        summary="Claims about whether a benchmark measures what it says.",
        required_evidence=(COMMAND_RESULT, FILE_QUOTE),
        min_probes=2,
        notes=("Resume and idempotence must be exercised, not read about.",
               "Report acceptance must be counted from a real run.",
               "Read what each bench emits; do not infer its keys from its neighbours."),
    ),
}

DEFAULT_ADAPTER = "review"


@dataclass
class Evidence:
    kind: str
    path: str | None = None
    start: int | None = None
    end: int | None = None
    quote: str | None = None
    command: str | None = None
    expect: str | None = None
    pattern: str | None = None
    globs: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class Claim:
    claim: str
    id: str = ""
    kind: str = ""
    severity: str = "medium"
    evidence: list[Evidence] = field(default_factory=list)
    falsification: dict[str, Any] | None = None
    unknowns: list[str] = field(default_factory=list)
    # Filled in by the gate, never by the model.
    verdict: str = ""
    detail: str = ""

    @property
    def high(self) -> bool:
        return str(self.severity).lower() in ("high", "critical")

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        out["evidence"] = [e.to_json() for e in self.evidence]
        return out


def run_dir(session_id: str, root: str | os.PathLike[str] = ".") -> Path:
    return Path(root) / ARTIFACT_ROOT / (session_id or "nosession")


def write_contract(contract: Contract, session_id: str, root: str = ".") -> Path:
    d = run_dir(session_id, root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "contract.json"
    p.write_text(json.dumps(contract.to_json(), indent=2) + "\n")
    return p


def load_contract(session_id: str, root: str = ".") -> Contract | None:
    p = run_dir(session_id, root) / "contract.json"
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text())
    except ValueError:
        return None
    known = {f for f in Contract.__dataclass_fields__}
    raw = {k: v for k, v in raw.items() if k in known}
    for key in ("required_evidence", "notes"):
        if key in raw:
            raw[key] = tuple(raw[key])
    return Contract(**raw)


def contract_for(adapter: str) -> Contract:
    return ADAPTERS.get(adapter, ADAPTERS[DEFAULT_ADAPTER])


def _evidence_from_dict(d: dict[str, Any]) -> Evidence:
    known = {f for f in Evidence.__dataclass_fields__}
    return Evidence(**{k: v for k, v in d.items() if k in known})


def load_claims(path: str | os.PathLike[str]) -> list[Claim]:
    """Read claims.jsonl. Unparseable lines are skipped, not fatal: a broken line is a gap the
    gate should report, not a crash that wedges the session."""
    out: list[Claim] = []
    p = Path(path)
    if not p.is_file():
        return out
    for line in p.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except ValueError:
            continue
        if not isinstance(raw, dict) or not raw.get("claim"):
            continue
        ev = [_evidence_from_dict(e) for e in raw.get("evidence") or [] if isinstance(e, dict)]
        out.append(Claim(
            claim=str(raw["claim"]),
            id=str(raw.get("id", "")),
            kind=str(raw.get("kind", "")),
            severity=str(raw.get("severity", "medium")),
            evidence=ev,
            falsification=raw.get("falsification"),
            unknowns=[str(u) for u in raw.get("unknowns") or []],
        ))
    return out


# The block form the model actually produces when refused. Kept in step with cc_verify's parser,
# which owns the regexes so there is one definition of a well-formed claim.
def claims_from_text(text: str) -> tuple[list[Claim], list[str]]:
    import cc_verify

    parsed, unknowns = cc_verify.parse_ledger(text)
    claims: list[Claim] = []
    for i, c in enumerate(parsed, 1):
        ev: list[Evidence] = []
        for item in c.get("evidence") or []:
            if item.get("kind") in EVIDENCE_KINDS:
                ev.append(Evidence(**{k: v for k, v in item.items()
                                      if k in Evidence.__dataclass_fields__}))
        claims.append(Claim(claim=c["claim"], id="c%d" % i, evidence=ev,
                            falsification=c.get("falsification"),
                            severity=c.get("severity") or _severity_hint(c["claim"])))
    return claims, unknowns


# Severity is the model's to declare in the JSON form; in the block form there is nowhere to put it,
# so the wording is used as a hint. Deliberately conservative: only unmistakable language promotes a
# claim to high, because promoting one wrongly demands a falsification the task may not need.
#
# Kept narrow after a false positive: it first matched a bare "lost" and promoted "output produced
# during that call is lost and replaced by an error row" -- a sentence describing what the code does
# on purpose -- which would have demanded a falsification probe for nothing. Only wording that
# asserts a defect promotes.
_HIGH = re.compile(r"\b(data loss|corrupt\w*|security (?:hole|flaw|issue)|vulnerab\w+|"
                   r"race condition|deadlock|silently (?:drops|discards|loses|fails))\b", re.I)


def _severity_hint(text: str) -> str:
    return "high" if _HIGH.search(text) else "medium"


def contract_markdown(contract: Contract) -> str:
    """The text injected into the session. Imperative and finite: it is charged to every turn.

    Only the evidence forms this adapter actually requires are described. Listing all four would
    add tokens to every turn for shapes the task cannot use, and give the model three more ways to
    answer the wrong question.
    """
    lines = [
        "TASK CONTRACT (%s). %s" % (contract.adapter, contract.summary),
        "",
        "This session is gated: an answer is refused unless its claims carry evidence that can be",
        "looked up. State each finding as a block, and nothing else in the final answer:",
        "",
        "CLAIM: <one sentence>",
        "EVIDENCE: <path>:<first_line>-<last_line>",
        "QUOTE:",
        "<the exact lines, copied from the file as you read it>",
    ]
    extra = {
        COMMAND_RESULT: ("EVIDENCE: command: <the command you ran> -> <text it printed>",),
        ABSENCE: ("EVIDENCE: absence: <pattern that must not be found> in <glob>",),
        LOG_MATCH: ("EVIDENCE: log: <path> ~ <regex that matches a real line>",),
    }
    wanted = [form for kind in contract.required_evidence for form in extra.get(kind, ())]
    if wanted:
        lines += ["", "This task also needs evidence of these kinds, one per EVIDENCE line:"]
        lines += wanted
    if contract.min_probes:
        lines.append("")
        lines.append("You must actually run at least %d command(s); describing one does not count."
                     % contract.min_probes)
    if contract.min_measurements:
        lines.append("At least %d measurements are required, so that a before and an after exist."
                     % contract.min_measurements)
    if contract.high_severity_needs_falsification:
        lines += ["", "For a claim you would call high severity, add both lines:",
                  "SEVERITY: high",
                  "FALSIFICATION: <the command you ran to try to disprove it, and what it printed>"]
    lines += [
        "",
        "Anything you could not establish goes at the end as `UNKNOWN: <one sentence>`.",
        "Saying you did not check is always allowed and never penalised. Guessing is not.",
    ]
    lines += ["", "Stance for this task:"] + ["- %s" % n for n in contract.notes]
    return "\n".join(lines)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("adapter", nargs="?", default=DEFAULT_ADAPTER,
                    help="one of: %s" % ", ".join(sorted(ADAPTERS)))
    ap.add_argument("--json", action="store_true", help="print the contract as JSON")
    args = ap.parse_args()
    c = contract_for(args.adapter)
    print(json.dumps(c.to_json(), indent=2) if args.json else contract_markdown(c))
    return 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main())
