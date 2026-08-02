#!/usr/bin/env python3
"""A Stop/SubagentStop hook that refuses an answer whose evidence does not survive being looked up.

The failure is premature closure: the model stops once it has a story that sounds complete, then
asserts the story with confident specifics. Prompting does not fix it -- this repository already
proved that for context discipline, and the same sentence applies here: advice is advisory, a hook
is arithmetic. So the stopping decision moves out of the model.

What the gate can and cannot judge. It cannot tell whether an answer is good. It can tell whether
each claim carries evidence, whether that evidence checks out against the files and commands the
session actually touched, and whether the adapter's floor was met. Everything it refuses on is
arithmetic; everything requiring judgement stays with the human reading the answer.

Three findings from the measured spike are built in rather than assumed (LOCAL_AGENT_OPS.md, §8):

* It blocks **once**. A refusal is a full turn with its own re-prefill, costing 1.8x to 2.5x the
  ungated stage -- 141 s and 432 s in the two arms. `stop_hook_active` short-circuits to success on
  re-entry, so `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` stays at its default and a nagging loop is
  impossible by construction.
* The refusal **enumerates every gap at once**, imperative and finite. At ~12 tok/s the model pays
  to read it.
* Told to re-read, the model **may not**: in arm 1 it answered the refusal from context with zero
  new tool calls, and its citations then drifted. So the gate never trusts an instruction to have
  been followed -- it re-reads the cited lines itself, and cross-checks each citation against the
  ranges the recorder saw.

Fail open, exactly like `cc-context-guard.py`: any unexpected condition allows the stop. A gate that
wedges a session because it crashed is worse than no gate. Kill switch: `touch /tmp/cc-depth-off`.

On the pass that follows a block (`stop_hook_active`), the gate still verifies and still writes
`gate.json` -- it just does not block. That is free observability: the final verdict of every gated
session is on disk, which is what the bench measurement reads.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cc_evidence  # noqa: E402
import cc_ledger  # noqa: E402
import cc_verify  # noqa: E402

OFF_SWITCH = Path("/tmp/cc-depth-off")


def allow() -> int:
    """Say nothing: the stop proceeds."""
    return 0


def block(reason: str) -> int:
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def _last_assistant_text(transcript: str) -> str:
    """The final answer, which in practice is where the claim blocks live.

    Scoped to the last assistant *message* rather than a window of recent text, because after a
    refusal the transcript holds both drafts. Judging the union of them re-reports gaps the model
    has already fixed, and double-counts its UNKNOWNs -- both observed in the false-premise arm.
    A message whose text carries no claim block is not an answer, so the window is the fallback.
    """
    messages: list[list[str]] = []
    for event in cc_evidence.iter_events(transcript):
        message = event.get("message") or {}
        if message.get("role") != "assistant":
            continue
        content = message.get("content")
        if isinstance(content, str):
            messages.append([content])
        elif isinstance(content, list):
            texts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
            if texts:
                messages.append(texts)
    if not messages:
        return ""
    latest = "\n".join(messages[-1])
    if "CLAIM:" in latest or "UNKNOWN:" in latest:
        return latest
    return "\n".join(t for chunk in messages[-4:] for t in chunk)


def _settled_text(transcript: str, budget: float = 2.0, quiet: float = 0.4) -> str:
    """The final answer, waited for.

    The Stop hook and the transcript write are a race, and the hook can win it: in the false-premise
    arm the last assistant event was stamped 23:45:24.728 and the gate wrote its verdict at
    23:45:24.779, so it judged the turn before the answer reached disk. It then reported "no claims
    were stated" about an answer holding a perfectly good one, and everything downstream -- the
    refusal, gate.json, the bench measurement -- inherited that.

    Waiting for the file to stop growing is not enough on its own, because at the moment the hook
    runs it may not have started growing yet. So the rule is a quiet period: the size must hold
    still for `quiet` seconds before the read is trusted, and `budget` caps the whole thing in case
    something is writing continuously. Costs 0.4 s on a turn that costs 110 s, and fails open by
    returning the last text it managed to read.
    """
    deadline = time.time() + budget
    last_size, since = -1, time.time()
    while True:
        try:
            size = os.path.getsize(os.path.expanduser(transcript))
        except OSError:
            return ""
        now = time.time()
        if size != last_size:
            last_size, since = size, now
        elif now - since >= quiet:
            break
        if now >= deadline:
            break
        time.sleep(0.1)
    return _last_assistant_text(transcript)


def _session_asserted_something(calls: list, text: str) -> bool:
    """Did this session do anything a claim could be made about?

    A session that only read one file and answered a factual question should not be forced to
    produce a ledger. One that edited files, ran commands, or wrote several paragraphs of
    conclusions should.
    """
    if any(c.tool in ("Edit", "Write", "MultiEdit", "NotebookEdit") for c in calls):
        return True
    if any(c.tool == "Bash" for c in calls):
        return True
    return len(text.split()) > 120


def evaluate(contract: cc_ledger.Contract, claims: list, unknowns: list[str],
             calls: list, root: str, check_coverage: bool = True) -> tuple[list[str], dict]:
    """Return (gaps, report). Gaps are what the refusal will say; report goes to gate.json.

    `check_coverage` exists for one caller: the scripted driver, which locates a transcript by
    reconstructing its path and can therefore fail to find it. A missing transcript looks exactly
    like a session that read nothing, and flagging every citation as unread would be a confident
    wrong answer from the component whose entire job is refusing those.
    """
    gaps: list[str] = []
    ranges = cc_evidence.covered_ranges(calls, root)
    probes = [c for c in calls if c.tool == "Bash" and _ran(c) and _is_probe(c.args.get("command"))]
    report: dict = {
        "adapter": contract.adapter,
        "claims": len(claims),
        "unknowns": unknowns,
        "probes_run": len(probes),
        "verdicts": [],
    }

    if len(claims) > contract.claim_cap:
        gaps.append("There are %d claims and the cap is %d. Merge or drop the marginal ones; a "
                    "list nobody can check is not evidence." % (len(claims), contract.claim_cap))
        claims = claims[:contract.claim_cap]

    kinds_seen: set[str] = set()
    for i, claim in enumerate(claims, 1):
        label = "claim %d (%s)" % (i, claim.claim[:70])
        if not claim.evidence:
            claim.verdict = "no-evidence"
            gaps.append("%s cites nothing. Quote the lines it rests on, or move it to UNKNOWN."
                        % label)
            report["verdicts"].append({"claim": claim.claim, "verdict": claim.verdict})
            continue

        for ev in claim.evidence:
            kinds_seen.add(ev.kind)
            verdict = _check(ev, root, calls)
            claim.verdict, claim.detail = verdict.kind, verdict.detail
            report["verdicts"].append({"claim": claim.claim, "kind": ev.kind,
                                       "verdict": verdict.kind, "detail": verdict.detail})
            if not verdict.ok:
                gaps.append("%s: %s -- %s" % (label, verdict.kind, verdict.detail))
            elif (check_coverage and ev.kind == cc_ledger.FILE_QUOTE and ev.path and ev.start
                  and not cc_evidence.covers(ranges, ev.path, ev.start, ev.end or ev.start, root)):
                # The quote matches the file, but nothing in this session ever read those lines.
                # Either it came from memory of another session, or it was reconstructed. Both are
                # citations the session cannot stand behind.
                gaps.append("%s cites %s:%s-%s, which no read in this session covered. Read it, "
                            "then quote what you see." % (label, ev.path, ev.start, ev.end))
                report["verdicts"][-1]["verdict"] = "uncovered"

        if contract.defects_only and _is_opinion(claim):
            gaps.append("%s says a design will be inconvenient and names nothing it does wrong. "
                        "Say what breaks, and when -- or write it as an UNKNOWN, which is a legal "
                        "answer." % label)

        if claim.high and contract.high_severity_needs_falsification:
            if not claim.falsification:
                gaps.append("%s is high severity and carries no falsification. Run the cheapest "
                            "command that would disprove it and report what it printed, or lower "
                            "the severity." % label)
            elif not _was_run(claim.falsification, probes):
                # Found by the gate reviewing itself: two high-severity claims passed with
                # falsifications that read "Ran a test script calling evaluate ...", in a session
                # whose own report said probes_run: 0. A field that is checked for existence and
                # not for truth is an invitation to write the sentence and skip the work.
                gaps.append("%s is high severity and its falsification describes a command this "
                            "session did not run%s. Run it and report what it printed, or lower "
                            "the severity."
                            % (label, "" if probes else " -- no command was run at all"))

    if not claims:
        gaps.append("No claims were stated. Write each finding as a CLAIM/EVIDENCE/QUOTE block, or "
                    "state UNKNOWN for what you could not establish.")

    missing = [k for k in contract.required_evidence if k not in kinds_seen]
    if missing and claims:
        gaps.append("This task type requires %s evidence and none was given. %s"
                    % (" and ".join(missing),
                       "An absence claim needs a search that returns nothing."
                       if cc_ledger.ABSENCE in missing else ""))
        if not unknowns:
            gaps.append("Nothing was listed as UNKNOWN either. If a required check was not run, "
                        "say so explicitly -- that is a legal answer.")

    if len(probes) < contract.min_probes:
        gaps.append("This task type requires %d command(s) actually run; %d ran. Running one "
                    "that fails is fine and informative; describing one is not."
                    % (contract.min_probes, len(probes)))

    if contract.min_measurements:
        measured = sum(1 for v in report["verdicts"]
                       if v.get("kind") in (cc_ledger.LOG_MATCH, cc_ledger.COMMAND_RESULT))
        if measured < contract.min_measurements:
            gaps.append("A performance claim needs a before and an after: %d measurement(s) "
                        "cited, %d required." % (measured, contract.min_measurements))

    return gaps, report


# Commands that only look around. None of them can come back and say "your claim is wrong", so
# counting them as probes would let the historical refactor proposal -- whose entire evidence was
# `ls`, `find` and three greps -- clear a floor written to stop exactly that. Searching is not
# thereby dismissed: a search that must return nothing is the `absence` evidence kind, checked on
# its own terms.
_LOOKING = ("ls", "find", "grep", "rg", "cat", "head", "tail", "wc", "pwd", "echo", "which",
            "tree", "file", "stat", "du", "df", "date")


# A call the client refused never reached a shell, so it is not a probe that failed -- it is a probe
# that did not happen. Everything else counts, including a non-zero exit: the command that proves a
# defect usually exits non-zero, and the refusal text has always said so.
_BLOCKED = re.compile(r"(?i)^\s*(context guard:|permission denied|.*blocked by hook)")


def _ran(call) -> bool:
    return call.ok or not _BLOCKED.match(str(call.text or ""))


def _was_run(falsification: str, probes: list) -> bool:
    """Does the falsification narrative correspond to a command this session actually ran?

    The program name alone is not enough, and the gate found that out about itself: reviewing this
    file, it claimed the check was "trivial to bypass because it only verifies that some command
    using the same executable was run", and proved it by running `python3 -c ...` and writing "I ran
    python3". Every session runs python3 or rg at some point, so that sentence would always pass.

    So: the program, and at least one further word of the same command, unless the command was a
    single word. Still generous enough for prose around a pasted command, which is what the contract
    asks for, and no longer satisfied by naming a program in the abstract.
    """
    if not probes:
        return False
    # The ledger hands this over as {"command": ...}, sometimes with the output beside it, and a
    # hand-written one arrives as a plain string. Reading it as a string when it is a dict raises,
    # and this gate fails open, so the check would have silently passed everything.
    if isinstance(falsification, dict):
        falsification = " ".join(str(v) for v in falsification.values())
    said = str(falsification).lower()
    for call in probes:
        for piece in re.split(r"\|\||&&|;|\|", str(call.args.get("command") or "")):
            words = piece.strip().split()
            if not words:
                continue
            program = os.path.basename(words[0]).lower()
            if program not in said:
                continue
            rest = [w.strip("'\"").lower() for w in words[1:]]
            # A flag is not enough. The gate's own review made this claim and proved it: sharing
            # `-c` with `python3 -c "..."` satisfied the check, and every second command has a -c.
            # Prefer a word that identifies *this* command -- a path, a name, a subcommand -- and
            # fall back to the flags only when the command consists of nothing else.
            named = [w for w in rest if len(w) > 2 and not w.startswith("-")]
            wanted = named or [w for w in rest if len(w) > 1]
            if not wanted or any(w in said for w in wanted):
                return True
    return False


# Four runs out of four produced the same shape of non-finding: "X is structurally coupled to Y,
# which will require duplication when adding Z", quoted from a signature or an env block. Telling
# the model not to in the adapter's stance did not stop it -- run four simply moved it from the
# proxy to the pipeline driver -- so it is a rule now.
#
# Lexical, and therefore narrow on purpose, like the severity hint above it. A real defect phrased
# this way ("coupled to Y, so a change in Y silently breaks Z") names the breakage, and the second
# half of the check looks for exactly that. What is left is the claim that asserts only future
# inconvenience, which is an opinion about a design and not a defect in it. The cost of being wrong
# here is one refusal round, in which the model states the consequence and moves on.
_OPINION = re.compile(r"(?i)\b(tightly |structurally )?coupled\b|\bnot (?:easily )?extensible\b|"
                      r"\b(?:difficult|hard|awkward|painful) to (?:add|extend|maintain|support|"
                      r"change|modify|test)\b|\bwill (?:require|need) (?:significant )?"
                      r"(?:duplication|rewriting|refactoring)\b|\bviolates? (?:the )?"
                      r"(?:single responsibility|separation of concerns)\b")
# A consequence is something that happens, to something, at runtime. If the sentence has one of
# these, it is claiming behaviour and not taste.
_CONSEQUENCE = re.compile(r"(?i)\b(silently|crash\w*|raise\w*|throw\w*|wrong|incorrect|stale|"
                          r"lost|loses|dropp?\w*|leak\w*|hang\w*|deadlock\w*|race|corrupt\w*|"
                          r"never (?:runs|fires|matches)|always (?:passes|fails)|off by|"
                          r"double[- ]count\w*|out of date|mismatch\w*|overflow\w*)\b")


def _is_opinion(claim) -> bool:
    if not _OPINION.search(claim.claim):
        return False
    return not _CONSEQUENCE.search(claim.claim) and not claim.falsification


def _is_probe(command: str | None) -> bool:
    """Could this command have failed in a way that disproves something?"""
    if not command:
        return False
    for piece in re.split(r"\|\||&&|;|\|", str(command)):
        first = piece.strip().split()
        if first and os.path.basename(first[0]) not in _LOOKING:
            return True
    return False


def _check(ev, root: str, calls: list):
    if ev.kind == cc_ledger.FILE_QUOTE:
        if not (ev.path and ev.start and ev.quote):
            return cc_verify.Verdict(cc_verify.UNVERIFIED, "incomplete file_quote")
        return cc_verify.file_quote(root, ev.path, ev.start, ev.end or ev.start, ev.quote)
    if ev.kind == cc_ledger.COMMAND_RESULT:
        return cc_verify.command_result(calls, ev.command or "", ev.expect or "")
    if ev.kind == cc_ledger.LOG_MATCH:
        return cc_verify.log_match(ev.path or "", ev.pattern or "")
    if ev.kind == cc_ledger.ABSENCE:
        return cc_verify.absence(root, ev.pattern or "", ev.globs or "")
    return cc_verify.Verdict(cc_verify.UNVERIFIED, "unknown evidence kind %r" % ev.kind)


def refusal(gaps: list[str], claims_path: Path) -> str:
    """Imperative and finite. The gap list, where to put the answer, and nothing else."""
    head = ("This answer is not accepted yet. %d thing(s) below are asserted without evidence that "
            "holds up. Fix them and finish; you will not be asked twice." % len(gaps))
    body = "\n".join("%d. %s" % (i, g) for i, g in enumerate(gaps, 1))
    tail = ("\nRe-read what you cite before quoting it -- quoting from memory is what produced "
            "half of these. Record the corrected findings in your reply as CLAIM/EVIDENCE/QUOTE "
            "blocks%s. Anything you cannot establish goes to UNKNOWN, which costs you nothing."
            % (", or in %s" % claims_path if claims_path.parent.is_dir() else ""))
    return "%s\n\n%s\n%s" % (head, body, tail)


def main() -> int:
    if OFF_SWITCH.exists():
        return allow()
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return allow()

    started = time.time()
    session = payload.get("session_id") or "nosession"
    root = payload.get("cwd") or os.getcwd()
    resumed = bool(payload.get("stop_hook_active"))

    # On SubagentStop the payload names two transcripts, and only one of them is the delegate's.
    # `transcript_path` is the parent's, which at that moment holds no answer at all -- the parent is
    # still inside its Agent call. Measured: the gate read it, found nothing, and refused a subagent
    # for "no claims" while the subagent's own file held a well-formed ledger. `agent_transcript_path`
    # is the delegate's, and is also the right scope for coverage: a sibling subagent's reads are not
    # evidence this one looked at anything.
    agent = payload.get("agent_id") or ""
    transcript = payload.get("agent_transcript_path") or payload.get("transcript_path") or ""

    contract = cc_ledger.load_contract(session, root)
    if contract is None:
        adapter = os.environ.get("CC_DEPTH_ADAPTER")
        if not adapter:
            return allow()      # ungated session: nothing was ever promised
        contract = cc_ledger.contract_for(adapter)

    calls = cc_evidence.collect(transcript) if transcript else []
    # The client hands over the answer it is about to accept, which removes the race with the
    # transcript write entirely. The settled read stays as the fallback for clients that do not.
    text = payload.get("last_assistant_message") or (_settled_text(transcript) if transcript else "")

    out_key = "%s/%s" % (session, agent) if agent else session
    claims_path = cc_ledger.run_dir(out_key, root) / "claims.jsonl"
    claims = cc_ledger.load_claims(claims_path)
    unknowns: list[str] = []
    if claims:
        unknowns = [u for c in claims for u in c.unknowns]
    else:
        claims, unknowns = cc_ledger.claims_from_text(text)

    if not claims and not _session_asserted_something(calls, text):
        return allow()          # a short factual answer is not a ledger-bearing one

    gaps, report = evaluate(contract, claims, unknowns, calls, root)
    report.update({
        "session": session,
        "agent": agent or None,
        "blocked": bool(gaps) and not resumed,
        "final_pass": resumed,
        "gaps": gaps,
        "ms": int((time.time() - started) * 1000),
    })
    out_dir = cc_ledger.run_dir(out_key, root)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "gate.json").write_text(json.dumps(report, indent=2) + "\n")
    except OSError:
        pass

    if resumed or not gaps:
        return allow()
    return block(refusal(gaps, claims_path))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Fail open, and say so on stderr where it does not reach the model.
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(0)
