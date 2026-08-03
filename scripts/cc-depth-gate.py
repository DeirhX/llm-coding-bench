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

import cc_diff  # noqa: E402
import cc_flow
import cc_flowstate
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
             calls: list, root: str, check_coverage: bool = True,
             answer: str = "", predicted: tuple = ()) -> tuple[list[str], dict]:
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
            # Seven claims came back established by actually running the guard and reporting what
            # it printed, and every one was told to "quote the lines it rests on" -- advice that
            # fits a claim about what the code says and not one about what it does. Both admissible
            # forms are named, or the stage is being asked to fake the wrong one.
            gaps.append("%s cites nothing. Quote the lines it rests on under a QUOTE header, or if "
                        "you established it by running something, write EVIDENCE: command: <the "
                        "command> -> <text it printed>. Otherwise move it to UNKNOWN." % label)
            report["verdicts"].append({"claim": claim.claim, "verdict": claim.verdict})
            continue

        for ev in claim.evidence:
            kinds_seen.add(ev.kind)
            verdict = _check(ev, root, calls)
            claim.verdict, claim.detail = verdict.kind, verdict.detail
            report["verdicts"].append({"claim": claim.claim, "kind": ev.kind,
                                       "verdict": verdict.kind, "detail": verdict.detail})
            if not verdict.ok and _read_showed(ev, calls, root):
                # The file moved under the stage. Reported, because the reader should know the
                # citation could not be checked against the file as it stands, but not counted
                # against the stage, which quoted what it was shown.
                report["verdicts"][-1]["verdict"] = "moved"
                report.setdefault("moved", []).append(ev.path)
            elif not verdict.ok:
                gaps.append("%s: %s -- %s" % (label, verdict.kind, verdict.detail))
            elif (check_coverage and ev.kind == cc_ledger.FILE_QUOTE and ev.path and ev.start
                  and not cc_evidence.covers(ranges, ev.path, ev.start, ev.end or ev.start, root)):
                # The quote matches the file, but nothing in this session ever read those lines.
                # Either it came from memory of another session, or it was reconstructed. Both are
                # citations the session cannot stand behind.
                gaps.append("%s cites %s:%s-%s, which no read in this session covered. Read it, "
                            "then quote what you see." % (label, ev.path, ev.start, ev.end))
                report["verdicts"][-1]["verdict"] = "uncovered"
            elif (check_coverage and ev.kind in (cc_ledger.ABSENCE, cc_ledger.LOG_MATCH)
                  and not _was_searched(ev, calls)):
                gaps.append("%s rests on %s that nothing in this session looked for. Run the search "
                            "and report what it printed." % (label, ev.kind.replace("_", " ")))
                report["verdicts"][-1]["verdict"] = "unsearched"

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

    # Length is what separates the two failures. A stage that stopped mid-thought writes one line
    # about what it is going to do next; a stage that answered in the wrong shape writes pages. The
    # first version of this told a seven-finding report that its turn had ended before it answered.
    if not claims and len(answer.strip()) < 500 and not re.search(
            r"(?m)^\W{0,4}(CLAIM|UNKNOWN)\b", answer):
        # Not a formatting complaint: the turn ended before the ledger began. Every first round of
        # every claims stage has finished on a sentence like "Now let me run the actual tests and
        # verify each claim", and telling it that no claims were stated reads as a quarrel about
        # blocks when what it needs to hear is that it stopped in the middle.
        gaps.append("Your turn ended before you answered -- the last thing you wrote was a sentence "
                    "about what you were going to do next. Write the ledger now: the findings you "
                    "have, each as a CLAIM with its EVIDENCE and QUOTE, and an UNKNOWN for whatever "
                    "you did not get to.")
    elif not claims:
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

    if contract.needs_red_green:
        pair, red = _red_then_green(probes)
        report["red_green"] = pair
        if not pair:
            gaps.append("Nothing here failed and then passed. Run the check that demonstrates the "
                        "problem before the change, then run that same command again afterwards -- "
                        "same words, so the two runs can be compared. A command that only ever "
                        "passed does not distinguish a fix from a no-op.")
        elif predicted and not _as_predicted(red, predicted):
            # The prediction was made before the code existed, which is the only moment at which
            # the model cannot choose the failure to suit the diff it has already written.
            gaps.append("The failing run of `%s` did not print what the plan said it would (%s). "
                        "Either the change is not aimed at the behaviour the plan named, or the "
                        "test is failing for some other reason. Show the predicted failure, or say "
                        "plainly that the prediction was wrong and why."
                        % (pair[:60], ", ".join(repr(p.get("expect")) for p in predicted)[:120]))
        elif not _behavioural(red):
            gaps.append("The failing run of `%s` failed because the code it calls did not exist "
                        "yet -- a missing argument, a missing name, an import. That is the test "
                        "agreeing with the diff's shape, not with its effect: written before the "
                        "change and run after it, it would pass either way. Make it fail on a "
                        "value that is wrong, and say which value." % pair[:60])

    if contract.needs_red_green:
        # Two things only the diff can answer, both taken from the first real implement run: whether
        # the tests it added assert anything about behaviour, and whether the change it made can
        # reach a production path at all.
        changed = cc_diff.diff(root)
        for hollow in cc_diff.hollow_tests(changed):
            gaps.append("%s. A test that only checks the wiring passes for any diff of that shape. "
                        "Assert the value the change is supposed to alter." % hollow)
        for inert in cc_diff.inert_parameters(changed, root):
            gaps.append("%s, so every production path still takes the default and behaves exactly "
                        "as before. Either pass it where the behaviour is wrong, or say plainly "
                        "that this change is preparation and the defect is still there." % inert)

    if contract.needs_prediction:
        made = cc_verify.predictions(answer)
        report["predictions"] = [p.get("expect") for p in made]
        if not made:
            # Two rounds of a real stage were spent being told this in the abstract. The line is
            # short enough to print, so print it: a model that cannot produce the shape from a
            # description of it can copy the shape.
            gaps.append("This plan commits to nothing. Add one line, exactly like this:\n"
                        "  PREDICT: command: <the command you will run> -> <a string its output "
                        "will contain while the defect is present>\n"
                        "For example: PREDICT: command: pytest tests/test_cash.py -q -> "
                        "available_cash_czk 1000000\n"
                        "The string must be one the run cannot print once the defect is gone, so a "
                        "wrong value qualifies and a stack trace does not. Without it the stage "
                        "that acts on this plan picks its own failure afterwards, and any change "
                        "can be made to fail somehow.")

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


def _was_searched(ev, calls) -> bool:
    """Did this session run something that could have produced this absence or log evidence?

    The gate checked a quote against the file *and* against what the session read, but checked an
    absence and a log match against the disk alone -- so a lucky guess passed, with no search behind
    it. Its own review put it plainly: those two "verify state on disk but not observation in the
    transcript". `absence` even says in its docstring that a claim of absence is only as good as the
    search behind it, which was true of the ripgrep the verifier runs and not of the model.

    Generous again: any command mentioning the pattern counts, and for a log, reading it counts too.
    """
    needle = str(getattr(ev, "pattern", "") or "").strip().lower()
    if not needle:
        return True
    for call in calls:
        if call.tool == "Bash" and needle in str(call.args.get("command") or "").lower():
            return True
        if (ev.kind == cc_ledger.LOG_MATCH and call.tool in ("Read", "Grep")
                and os.path.basename(str(ev.path or "")).lower()
                in str(call.args.get("file_path") or call.args.get("path") or "").lower()):
            return True
    return False


# What a test prints when the code it calls is not there yet, as opposed to being wrong. The first
# implement run produced all of it: three tests whose red was `TypeError: _put_cash_requirement()
# got an unexpected keyword argument 'holdings'`, which is the test asserting that the diff has the
# shape the diff has. Every one of them passed the red/green check and none of them touched
# behaviour -- the change threaded a parameter that no caller ever passes.
_INTERFACE = re.compile(r"(?i)unexpected keyword argument|takes \d+ positional argument|"
                        r"ModuleNotFoundError|ImportError|IndentationError|SyntaxError|"
                        r"AttributeError: (?:module|type object|'\w+' object) .*has no attribute|"
                        r"NameError: name|no tests ran|errors? (?:during|while) collecting")
# An assertion that failed is the shape of a test that ran and disagreed with what it found.
_BEHAVIOURAL = re.compile(r"(?i)AssertionError|^E\s+assert|assert\w* .* (?:!=|==|not in)|"
                          r"Expected .* but got|\bmismatch\b", re.M)


def _as_predicted(red, predicted: tuple) -> bool:
    """Does the failure the session produced match the one its plan committed to?

    Substring, on the output only: the command is already matched by the red/green pair, and asking
    for two matches of the same thing would only add a way to be wrong about whitespace.
    """
    text = str(getattr(red, "text", "") or "")
    if not text.strip():
        return True          # unreadable output is not evidence of a mismatch, as above
    return any(str(p.get("expect") or "").strip() in text for p in predicted if p.get("expect"))


def _behavioural(red) -> bool:
    """Did the failing run fail on a value, or on the code not being written yet?

    Generous where it is uncertain: no output at all counts as behavioural, because a check that
    cannot read the failure should not be the thing that refuses the answer. It only fires when the
    output says plainly that a name or a signature was missing and says nothing about a value.
    """
    text = str(getattr(red, "text", "") or "")
    if not text.strip():
        return True
    return bool(_BEHAVIOURAL.search(text)) or not _INTERFACE.search(text)


def _red_then_green(probes: list) -> tuple[str | None, object]:
    """The one command that failed and later passed, with the failing call, or (None, None).

    This is the implement adapter's whole evidential basis, and it is deliberately literal: the same
    command string, an early run that failed, a later run that did not. Normalising harder -- same
    program, same test file, close enough -- would accept the case this exists to catch, where the
    "after" run quietly narrows the selection to the test that was made to pass.

    Cost of the strictness is a session that retypes its command slightly and is asked once to run
    it again verbatim. Cost of the looseness is a green tick for a fix that fixed nothing.
    """
    outcomes: dict[str, list] = {}
    for call in probes:
        command = " ".join(str(call.args.get("command") or "").split())
        if command:
            outcomes.setdefault(command, []).append(call)
    for command, runs in outcomes.items():
        results = [bool(c.ok) for c in runs]
        if False in results and True in results[results.index(False) + 1:]:
            return command, runs[results.index(False)]
    return None, None


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


def _read_showed(ev, calls: list, root: str) -> bool:
    """Did a Read in this session show this quote in this file, whatever the file says now?

    A file edited while a stage was reading it makes every citation of it look like fabrication:
    the quote is verbatim, the address has moved, and the verdict says "not present in". That
    happened twice in one afternoon here, to a stage that had done nothing wrong.

    A Read result is written by the client, not by the model, so it cannot be arranged. Bash output
    can be -- `echo` prints whatever it is given -- so only Read counts.

    Showing the quote is not enough on its own: a stage that cites real text at line 200 when it
    sits at line 4 has done the thing the line numbers exist to prevent, and the Read shows that
    text too. So the file must also have changed since the Read, which the Read itself proves --
    lines it displayed that the file no longer has anywhere.
    """
    if ev.kind != cc_ledger.FILE_QUOTE or not (ev.path and ev.quote):
        return False
    wanted = [ln.strip() for ln in ev.quote.splitlines() if ln.strip()]
    if not wanted:
        return False
    for call in calls:
        if call.tool != "Read" or not call.text:
            continue
        named = str(call.args.get("file_path") or "")
        if not named.endswith(ev.path.lstrip("./")) and Path(named).name != Path(ev.path).name:
            continue
        if all(line in call.text for line in wanted) and _drifted(call, root, ev.path):
            return True
    return False


_GUTTERED = re.compile(r"^\s{0,8}\d{1,6}(?:\s*[|:>]|\s)(?P<code>.*)$")


def _drifted(call, root: str, path: str) -> bool:
    """Does the file no longer hold what this Read displayed?"""
    try:
        now = Path(root, path).read_text(errors="replace")
    except OSError:
        return False
    shown = []
    for line in call.text.splitlines():
        seen = _GUTTERED.match(line)
        body = (seen.group("code") if seen else line).strip()
        if len(body) > 12:
            shown.append(body)
    if not shown:
        return False
    return any(line not in now for line in sorted(shown, key=len, reverse=True)[:20])


def _check(ev, root: str, calls: list):
    if ev.kind == cc_ledger.FILE_QUOTE:
        if ev.path and ev.quote and not ev.start:
            # A quote with no line numbers: find it. The lines are set on the evidence so that the
            # coverage check downstream still has an address to work with.
            where = cc_verify.locate(root, ev.path, ev.quote)
            if where is None:
                return cc_verify.Verdict(cc_verify.FAIL, "quote not present in %s" % ev.path)
            ev.start, ev.end = where
        if not (ev.path and ev.start and ev.quote):
            # "incomplete file_quote" told seven cited findings nothing they could act on. What is
            # missing is almost always the quote: the stage named the file and the lines and then
            # described them, which is the habit the quote exists to break.
            missing = ("the lines themselves -- paste them under a QUOTE header, exactly as they "
                       "appear, instead of describing them" if ev.path and ev.start
                       else "a file and a line range")
            return cc_verify.Verdict(cc_verify.UNVERIFIED, "this citation is missing " + missing)
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
    # The tail used to offer claims.jsonl as an alternative home for the ledger, which is why a
    # stage put it there and summarised it in prose: the file is read only by the scripted driver,
    # and in a flow the next stage sees nothing but the message.
    tail = ("\nRe-read what you cite before quoting it -- quoting from memory is what produced "
            "half of these. Record the corrected findings in the message you finish with, as "
            "CLAIM/EVIDENCE/QUOTE blocks, in full. Anything you cannot establish goes to UNKNOWN, "
            "which costs you nothing.")
    return "%s\n\n%s\n%s" % (head, body, tail)


# How many times a session may be pushed back into its flow before it is let go. One is too few:
# a three-stage flow needs at least one push per stage, and a model that stops to "wait" for a
# subagent it has already been handed the report from will spend several. Unbounded is a hang.
NUDGE_LIMIT = 8


def _stage_of(state: dict, agent: str, payload: dict) -> str:
    """Which stage of the flow this subagent is, if any.

    The launch is recorded by the PreToolUse hook before the agent exists, so the running stage is
    the one with no verdict yet. With one stage in flight at a time -- which the same hook enforces
    -- that is unambiguous.
    """
    if not state.get("flow"):
        return ""
    for entry in reversed(state.get("stages", [])):
        if entry.get("verdict") is None:
            if agent and entry.get("agent") and entry["agent"] != agent:
                continue
            return entry.get("stage", "")
    return ""


def _digest(text: str, limit: int = 1200) -> str:
    """What a stage hands the next one: its claims, not its prose."""
    kept = [line for line in (text or "").splitlines()
            if line.startswith(("CLAIM:", "EVIDENCE:", "UNKNOWN:", "PREDICT:", "CHANGE:"))]
    return "\n".join(kept)[:limit]


# How long the stop hook will sit waiting for a stage to report before answering the session. Long
# enough to cover the gap between a session finishing its turn and its stage finishing its work,
# short enough that a hook the client has given up on is not still sleeping.
WAIT_FOR = float(os.environ.get("CC_FLOW_WAIT", "90"))


def _await_stage(session: str, root: str, state: dict) -> tuple[dict, list[str]]:
    """Wait here for the stage in flight, and say what is still running when we give up."""
    deadline = time.time() + WAIT_FOR
    while time.time() < deadline:
        in_flight = cc_flowstate.running(state)
        if not in_flight:
            return state, []
        time.sleep(2.0)
        state = cc_flowstate.load(session, root)
    return state, cc_flowstate.running(state)


def main() -> int:
    # A file is not a switch when the thing being switched off can make files. Honoured only in a
    # session launched to honour it; the environment of a hook is the one thing a stage cannot
    # reach, because the client spawns each hook fresh from its own.
    if OFF_SWITCH.exists() and os.environ.get("CC_DEPTH_LIFTABLE") == "1":
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

    # A stage of a flow is judged as that stage, not as the session it belongs to. The plan of a
    # change cannot be held to red/green -- nothing has run yet -- and holding it to the session's
    # contract is how a plan that committed to nothing came to be accepted and then built upon.
    state = cc_flowstate.load(session, root)
    stage = _stage_of(state, agent, payload)
    # The client hands over the answer it is about to accept, which removes the race with the
    # transcript write entirely. The settled read stays as the fallback for clients that do not.
    text = payload.get("last_assistant_message") or (_settled_text(transcript) if transcript else "")

    # A session that ends in the middle of a flow has answered from one stage of three. The first
    # one to run did exactly that: the survey reported, the orchestrator relayed it, and the flow
    # stopped two stances short of an answer anybody should act on.
    if os.environ.get("CC_FLOW_TRACE"):
        try:
            with open(os.environ["CC_FLOW_TRACE"], "a") as fh:
                fh.write(json.dumps({"hook": payload.get("hook_event_name"),
                                     "agent": agent, "resumed": resumed,
                                     "flow": state.get("flow"),
                                     "nudges": state.get("nudges")}) + "\n")
        except OSError:
            pass

    # `stop_hook_active` is not consulted here. Its purpose is to stop a Stop hook looping, and a
    # bounded nudge count does that job better: the flag was already set on the parent's stop after
    # a subagent had finished, so treating it as "we have pushed once already" meant never pushing
    # at all. Measured: a flow that completed its survey and then ended, with the counter at zero.
    if not agent and state.get("flow"):
        # A stage still marked in flight when the parent stops is one whose subagent went away:
        # a parent cannot finish a turn while its own Task call is outstanding. Measured on the
        # second flow: the orchestrator said it would wait for the survey, then answered from a
        # file it had read itself while the survey was still going.
        abandoned = cc_flowstate.forget_running(state)
        if abandoned:
            cc_flowstate.save(state, session, root)
        in_flight = cc_flowstate.running(state)
        if in_flight:
            # Blocking a stop cannot make a session wait. It can only make it speak again, and it
            # did: told to wait for the survey, it said "I'll wait for it to report" and stopped,
            # eighty-four times over ten minutes and seventy-six thousand tokens. So the waiting is
            # done here instead, in the hook, where waiting is a thing a process can actually do.
            state, in_flight = _await_stage(session, root, state)
        # After the wait, and not before it: a stage that reported while we waited has changed what
        # comes next, and answering from the older view told a session that survey had run and that
        # survey had not.
        left = cc_flowstate.next_stage(state)
        nudges = int(state.get("nudges", 0))
        if in_flight and nudges < NUDGE_LIMIT:
            # Stopping with a stage still reading is the ordinary shape of this: the launch returns
            # a task and the turn ends while the work goes on. What the session must not do is take
            # that as the stage having failed, which it did -- killing the task, then answering from
            # a file it had read itself.
            state["nudges"] = nudges + 1
            cc_flowstate.save(state, session, root)
            return block(
                "The %s stage is still running. Call the TaskOutput tool for it with `block` "
                "set to true and a timeout of several minutes -- that is how you wait; saying you "
                "will wait and then stopping is not. It has not failed and it is not stuck."
                % ", ".join(in_flight))
        given_up = [st.name for st in cc_flow.flow_for(state["flow"]) or []
                    if cc_flowstate.exhausted(state, st.name)
                    and st.name not in cc_flowstate.done(state)]
        if not left and given_up and not state.get("disclosed"):
            # A flow that gave up on a stage still produces an answer, and without this the answer
            # reads like any other: three refused rounds of claims are invisible to whoever asked
            # for the review. The gaps are the most useful thing the flow learned, so they are made
            # part of the reply rather than left in a state file nobody opens.
            state["disclosed"] = True
            cc_flowstate.save(state, session, root)
            gaps = [g for e in cc_flowstate.refused(state) for g in e.get("gaps", [])]
            return block(
                "The %s stage was refused %d times and the flow has given up on it, so your answer "
                "must say so rather than reading like a finished review. Say which stage it was, "
                "and state plainly what was never established: %s. Then finish."
                % (", ".join(given_up), cc_flowstate.ROUND_CAP,
                   " | ".join(g[:160] for g in gaps[:6]) or "nothing was recorded"))
        if left and nudges < NUDGE_LIMIT:
            done = cc_flowstate.done(state)
            state["nudges"] = nudges + 1
            cc_flowstate.save(state, session, root)
            return block(
                "The %s flow is not finished. %s run; %s has not. Launch a subagent whose "
                "prompt begins with `STAGE: %s`. Nothing is running now, so there is nothing to "
                "wait for -- launch it, then read its output until it reports. Answering now would "
                "give me one stage's view of this, and "
                "the stages after it exist because that view is the one that has been wrong before."
                % (state["flow"],
                   ("%s %s" % (", ".join(done), "have" if len(done) > 1 else "has"))
                   if done else "No stage has",
                   left, left))

    contract = None
    if stage:
        # Some stages are not judged at all. A survey is an inventory, and holding an inventory to
        # a contract that wants claims refuses it for having made none -- which is the one thing it
        # was told to do. Measured on the first flow that ran: refused at 59 seconds, for obeying.
        running = cc_flow.stage_in(state.get("flow", ""), stage)
        if running is not None and not running.verify:
            cc_flowstate.record_verdict(state, stage, [], agent)
            state["nudges"] = 0
            for entry in reversed(state.get("stages", [])):
                if entry.get("stage") == stage and not entry.get("summary"):
                    entry["summary"] = _digest(text, limit=2000) or text[-1500:]
                    break
            cc_flowstate.save(state, session, root)
            return allow()
        contract = cc_ledger.contract_for(cc_flow.adapter_for(state.get("flow", ""), stage))
    if contract is None:
        contract = cc_ledger.load_contract(session, root)
    if contract is None:
        adapter = os.environ.get("CC_DEPTH_ADAPTER")
        if not adapter:
            return allow()      # ungated session: nothing was ever promised
        contract = cc_ledger.contract_for(adapter)

    calls = cc_evidence.collect(transcript) if transcript else []

    out_key = "%s/%s" % (session, agent) if agent else session
    claims_path = cc_ledger.run_dir(out_key, root) / "claims.jsonl"
    claims = cc_ledger.load_claims(claims_path)
    unknowns: list[str] = []
    if claims:
        unknowns = [u for c in claims for u in c.unknowns]
    else:
        claims, unknowns = cc_ledger.claims_from_text(text, root)

    if not claims and not _session_asserted_something(calls, text):
        return allow()          # a short factual answer is not a ledger-bearing one

    gaps, report = evaluate(contract, claims, unknowns, calls, root, answer=text)
    if stage:
        # What the next stage may do turns on this verdict, so it is written where the hook that
        # admits the next launch can read it rather than left in the conversation.
        cc_flowstate.record_verdict(state, stage, gaps, agent, text)
        state["nudges"] = 0     # a stage reported, so the budget for pushing is not being spent
        for entry in reversed(state.get("stages", [])):
            if entry.get("stage") == stage and not entry.get("summary"):
                entry["summary"] = _digest(text)
                break
        cc_flowstate.save(state, session, root)
        report["stage"] = stage
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
