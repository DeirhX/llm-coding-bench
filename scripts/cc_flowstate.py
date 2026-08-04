"""Where a flow has got to, on disk, so that nothing has to trust the model's memory of it.

An interactive session runs the stages as native subagents, which means the decision to launch the
next one is made by the model. That is fine for sequencing and useless for enforcement: the model is
the thing being held to a standard, and the standard includes "do not build on a plan that was
refused". The scripted driver settles this with a `break` in a loop it owns. Here the loop is the
conversation, so the state it would have kept is kept in a file and read by a PreToolUse hook, which
can refuse a launch the way the loop refuses a stage.

One file per session, next to everything else the gate writes.
"""

from __future__ import annotations

import atexit
import fcntl
import json
import os
import time
from pathlib import Path

import cc_flow
import cc_ledger


def path_for(session: str, root: str) -> Path:
    return cc_ledger.run_dir(session, root) / "flow.json"


# The flow file is read and written by separate hook processes, and a client that issues three tool
# calls in one turn runs three of them at once. Unserialised, the slowest one saves what it read
# before the others changed anything, and whatever they recorded is gone. Run 19 lost the launch of
# its claims stage that way -- three refusals in the same second, the launch admitted and recorded
# three seconds later, and a straggler from the refusals wrote the launch back out of existence. The
# stage then ran 157 tool calls that the flow had no record of, so the guard read every one of them as
# the orchestrator idling and told the working subagent to launch itself. 77 refusals, and a session
# deadlocked against a stage it was already running.
_HELD = None                    # the lock this process holds between load() and save()


def _lock(session: str, root: str, wait: float = 5.0) -> None:
    """Hold the flow file from load() until save(), so a read-modify-write is not interleaved."""
    global _HELD
    if _HELD is not None:
        return
    try:
        out = path_for(session, root)
        out.parent.mkdir(parents=True, exist_ok=True)
        fh = open(str(out) + ".lock", "a+")
    except OSError:
        return
    limit = time.time() + wait
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _HELD = fh
            return
        except OSError:
            if time.time() >= limit:
                # A lost update is a bug; a hook that never returns is a hung session. Prefer the bug.
                fh.close()
                return
            time.sleep(0.02)


def _unlock() -> None:
    global _HELD
    if _HELD is None:
        return
    held, _HELD = _HELD, None
    try:
        fcntl.flock(held, fcntl.LOCK_UN)
    except OSError:
        pass
    held.close()


def release() -> None:
    """Let go before doing anything slow. Held across a wait, the lock stops the stage being waited
    for from reporting: its hooks block, time out, and write unserialised after all."""
    _unlock()


def peek(session: str, root: str) -> dict:
    """Read the flow without taking the lock, for anything that polls it in a loop.

    The Stop hook waits minutes for a stage by reading this file over and over. Doing that under the
    lock would stop every other hook for as long as the wait.
    """
    try:
        return json.loads(path_for(session, root).read_text())
    except (OSError, ValueError):
        return {}


def load(session: str, root: str) -> dict:
    _lock(session, root)
    try:
        return json.loads(path_for(session, root).read_text())
    except (OSError, ValueError):
        return {}


def save(state: dict, session: str, root: str) -> None:
    out = path_for(session, root)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2) + "\n")
        os.replace(tmp, out)        # a reader never sees half a file
    except OSError:
        pass
    finally:
        _unlock()


def begin(flow: str, task: str, session: str, root: str) -> dict:
    """Start a flow, discarding any earlier one in this session."""
    state = {"flow": flow, "task": task, "started": time.time(), "stages": []}
    save(state, session, root)
    return state


# How long a launch may be outstanding before a relaunch is read as replacing it rather than
# duplicating it. A stage that reports takes tens of seconds to a few minutes; one that has said
# nothing for this long has died without its stop ever firing, and holding its place would deadlock
# the flow -- there is no other way back from a subagent that vanishes.
STALE_AFTER = 600.0


# A reopened round is given far less rope. It exists on the assumption that a refused subagent goes
# round again; when it does not -- and headless it usually does not, it simply exits -- the entry
# holds the flow open with nothing behind it. A real second round makes a tool call within seconds,
# so silence here means the worker is gone rather than thinking.
REOPENED_STALE = 120.0


def _silent(entry: dict) -> bool:
    """Has this launch shown no sign of life long enough to be treated as gone?

    Measured from the last tool call charged to it rather than from the launch, because a stage that
    is reading is plainly alive however long it has been at it. A round that had spent nine minutes
    was given up on and relaunched under the older rule, which counted from the launch alone.
    """
    last = max(float(entry.get("active") or 0), float(entry.get("launched") or 0))
    limit = REOPENED_STALE if entry.get("reopened") and not entry.get("calls") else STALE_AFTER
    return time.time() - last >= limit


def record_launch(state: dict, stage: str, agent: str = "") -> dict:
    state.setdefault("stages", []).append(
        {"stage": stage, "agent": agent, "launched": time.time(), "verdict": None, "gaps": []})
    return state


# How much of a refused ledger is handed back to the round that must fix it. Long enough for the
# eighteen-claim ledgers this produces, short enough not to crowd out the file it is about.
LEDGER_KEPT = 12_000

# How many verified findings are carried out of a round. The claim cap is twelve, so this keeps every
# finding a round could legally have made.
STOOD_KEPT = 12


def record_verdict(state: dict, stage: str, gaps: list, agent: str = "", answer: str = "",
                   stood: list | None = None, reopen: bool = True) -> dict:
    """Attach a gate verdict to the most recent launch of `stage`.

    A refusal does not end a subagent, it sends it round again, so the same launch reports twice --
    once refused, once with whatever it did about that. Without the second branch here the later
    verdict lands nowhere: a stage that was refused and then fixed itself stays refused forever, and
    for a blocking stage that means a flow that can never continue.
    """
    # Oldest first: whoever was launched first is the one now reporting, absent any better
    # evidence. The launch cannot know the agent id -- it is assigned afterwards -- so this is the
    # only ordering available, and it matters whenever two entries are somehow outstanding.
    pending = [e for e in state.get("stages", [])
               if e.get("stage") == stage and e.get("verdict") is None]
    pending.reverse()
    same = [e for e in state.get("stages", [])
            if e.get("stage") == stage and (not agent or e.get("agent") == agent)]
    entry = pending[-1] if pending else (same[-1] if same else None)
    if entry is not None:
        entry["verdict"] = "refused" if gaps else "accepted"
        entry["gaps"] = list(gaps)
        entry["agent"] = agent or entry.get("agent", "")
        entry["finished"] = time.time()
        if gaps and answer:
            entry["answer"] = answer[-LEDGER_KEPT:]
        if stood:
            entry["stood"] = list(stood)[:STOOD_KEPT]
        entry["rounds"] = int(entry.get("rounds", 1)) + (0 if pending else 1)
        if gaps and reopen and not exhausted(state, stage):
            # A refusal is not the end of the subagent, so the stage is still in flight and must
            # still look it. Closing it here told the flow guard that nothing was running, and the
            # guard then read the working stage as an idle orchestrator and ordered it to delegate
            # its own work to a subagent. It obliged, reported nothing, and was refused again.
            #
            # Once the round cap is reached there is nothing left to reopen for. Run 22 reopened its
            # claims stage on the third refusal, and the worker kept going for another 54 tool calls
            # and five minutes towards a verdict the flow had already given up on reading, while the
            # session waited on it to write the findings that had survived.
            record_launch(state, stage, entry.get("agent", ""))
            state["stages"][-1]["reopened"] = True
        elif gaps and not reopen:
            # The worker is being killed as this is written, so there is nothing still in flight to
            # keep a place for. Reopening it here left the flow reporting a stage in progress that
            # had just been stopped on its own instruction, and the session was sent to wait on it.
            state["stages"] = [e for e in state.get("stages", [])
                               if e.get("stage") != stage or e.get("verdict") is not None]
        elif gaps:
            # Earlier rounds left launches of their own outstanding, because a verdict lands on the
            # oldest pending entry and a reopen adds another. Once the stage is given up on, none of
            # them is in flight in any sense worth reporting, and leaving them there says a stage is
            # running that nothing will read. Dropped rather than given a verdict of their own, so
            # that what counts as refused, accepted or done is unchanged.
            state["stages"] = [e for e in state.get("stages", [])
                               if e.get("stage") != stage or e.get("verdict") is not None]
    return state


# Findings restated between rounds are not merged by how alike they read, and the numbers are why.
# Measured on run 21's own ledgers plus two claims that differ only in which way round they are:
#
#   the same finding reworded    raw 0.55-0.60   sorted-token 0.55-0.88
#   "blocks touch but not rm" /
#   "blocks rm but not touch"    raw 0.82        sorted-token 1.00
#
# A paraphrase shares less text with its original than a logical inverse shares with it, so every
# threshold that merges the duplicates merges the opposites first. Only identical text is merged,
# which means a restatement can appear twice -- cosmetic, where quietly dropping a finding would be
# the disease this harness exists to treat.
def salvage(state: dict, stage: str) -> list[dict]:
    """The findings of `stage` that passed the gate, gathered across every round it was given.

    Being given up on is a verdict about a stage, not about each thing it said. Run 21's claims stage
    was refused three times and abandoned, having proved six findings along the way -- and the flow
    delivered none of them, because nothing looked below the level of the stage. Deduplicated on the
    claim text with the latest round winning, since a reopened round restates what it already had.
    """
    kept: list[dict] = []
    for entry in state.get("stages", []):
        if entry.get("stage") != stage:
            continue
        for finding in entry.get("stood") or []:
            text = (finding.get("claim") or "").strip()
            if not text:
                continue
            twin = next((k for k in kept if k.get("claim", "").strip() == text), None)
            if twin is None:
                kept.append(dict(finding))
            elif len(finding.get("cites") or []) >= len(twin.get("cites") or []):
                # A later round said it again, better cited. Keep that one.
                kept[kept.index(twin)] = dict(finding)
    return kept


def done(state: dict) -> list[str]:
    return [e["stage"] for e in state.get("stages", []) if e.get("verdict") == "accepted"]


def refused(state: dict) -> list[dict]:
    return [e for e in state.get("stages", []) if e.get("verdict") == "refused"]


# How many tool calls one round of a stage may spend before it must answer. A thorough review of a
# three-hundred-line file runs to forty or fifty. The number exists because a claims stage reached
# 387, re-reading the same files and saying "I need to re-read the files I'm citing before quoting
# them" each time: the token cap bounds one answer, and nothing bounded the reading.
CALL_BUDGET = 140


def spend(state: dict, stage: str) -> int:
    """Charge one tool call to the round of `stage` now in flight, and say what it has spent."""
    for entry in reversed(state.get("stages", [])):
        if entry.get("stage") == stage and entry.get("verdict") is None:
            entry["calls"] = int(entry.get("calls", 0)) + 1
            entry["active"] = time.time()
            return entry["calls"]
    return 0


# How many refusals a stage may ignore before it is treated as gone rather than working. A stage
# that has been told to answer twenty-five times running and is still calling tools is not going to
# start: run 7's stage took 194 of them and never answered, run 10's took 246, run 24's took 220 of
# a message it could not even see. Twenty-five is far enough past a stage that pauses to think and
# well short of the hundreds these cost.
DEAF_AFTER = 25


def deaf(state: dict) -> list[str]:
    """Stages that have gone on working through refusal after refusal.

    The flow cannot stop a subagent -- no hook can, a refused call is just a call that returns a
    refusal -- so a stage that will not stop has to be ended from outside, by the session that
    launched it. This is what tells the session which one.
    """
    return [e["stage"] for e in state.get("stages", [])
            if e.get("verdict") is None and int(e.get("denied", 0)) > DEAF_AFTER]


def refused_once_more(state: dict, stage: str) -> int:
    """Note that this stage was refused again, and say how many times it now has been."""
    for entry in reversed(state.get("stages", [])):
        if entry.get("stage") == stage and entry.get("verdict") is None:
            entry["denied"] = int(entry.get("denied", 0)) + 1
            return entry["denied"]
    return 0


def overspent(state: dict, stage: str) -> int:
    """How far past its budget the round of `stage` now in flight has gone, or 0.

    Read by both PreToolUse hooks, because only one of their refusals reaches the model and which
    one is not documented. Run 24's flow guard denied 220 consecutive calls with `spent=280
    allowed=60` in its own trace, and the survey saw the context guard's message every time -- 256
    identical refusals of a command it kept rewriting to sneak past, while the message telling it
    to stop and answer went to nobody. Ordering was tried as the fix once already, in the other
    direction, on the same reasoning. Saying the same thing from both mouths does not depend on
    knowing which one is heard.
    """
    import cc_flow
    for entry in reversed(state.get("stages", [])):
        if entry.get("stage") == stage and entry.get("verdict") is None:
            known = cc_flow.stage_in(state.get("flow") or "", stage)
            allowed = known.budget if known is not None and known.budget else CALL_BUDGET
            return max(0, int(entry.get("calls", 0)) - allowed)
    return 0


def running(state: dict) -> list[str]:
    return [e["stage"] for e in state.get("stages", []) if e.get("verdict") is None]


# How many times one stage may be refused before the flow stops asking it. Three, because the first
# refusal is usually about shape and the second about evidence, and a stage that has not answered
# either by the third is not going to. Without a cap a claims stage was refused, relaunched, and
# refused again until the nudge limit ended the session with nothing judged at all.
ROUND_CAP = 3
# A round that never wrote a ledger at all -- it narrated what it was about to do and stopped -- is
# not one of the three. Two of run 7's three claims rounds went that way, so the stage was given up
# on having been judged on its evidence exactly once. Those rounds are capped separately, and
# lower, because a stage that cannot produce the shape twice running will not produce it a third
# time either.
SILENT_CAP = 2


def _answered(entry: dict) -> bool:
    return "CLAIM" in (entry.get("answer") or "") or bool(entry.get("summary"))


def exhausted(state: dict, stage: str) -> bool:
    refused = [e for e in state.get("stages", [])
               if e.get("stage") == stage and e.get("verdict") == "refused"]
    judged = [e for e in refused if _answered(e)]
    return len(judged) >= ROUND_CAP or len(refused) - len(judged) >= SILENT_CAP


def next_stage(state: dict) -> str | None:
    """The stage that should run now, or None when the flow is over -- complete or given up on."""
    flow = state.get("flow", "")
    stages = cc_flow.flow_for(flow) or []
    finished = set(done(state))
    for stage in stages:
        if stage.name in finished:
            continue
        if not exhausted(state, stage.name):
            return stage.name
        # A stage nobody could satisfy. If what follows depends on it there is nothing honest left
        # to do, so the flow ends and its gaps are what the session has to report.
        if stage.blocking:
            return None
    return None


def admits(state: dict, stage: str) -> tuple[bool, str]:
    """Whether `stage` may be launched now, and why not when it may not.

    Three refusals, each of them something a real run did. Launching a stage the flow does not
    contain, launching one out of order -- which for the change flow means implementing a plan that
    was never accepted -- and launching anything at all after a blocking stage was refused.
    """
    flow = state.get("flow", "")
    stages = cc_flow.flow_for(flow)
    if not stages:
        return False, ("No flow is running. Start one by asking for a review or a change in the "
                       "usual way; a stage on its own has nothing to be a stage of.")

    known = [s.name for s in stages]
    if stage not in known:
        return False, ("There is no %s stage in the %s flow. Its stages, in order, are: %s."
                       % (stage, flow, ", ".join(known)))

    for entry in refused(state):
        # Rerunning the refused stage is the way out of this, not another instance of it.
        blocking = cc_flow.stage_in(flow, entry["stage"])
        if blocking is not None and blocking.blocking and stage != entry["stage"]:
            return False, ("The %s stage was refused, and every stage after it would be acting on "
                           "what it produced. Fix that first -- rerun %s and satisfy it -- rather "
                           "than carrying its gaps forward. It was refused because: %s"
                           % (entry["stage"], entry["stage"],
                              " ".join(entry.get("gaps", []))[:400]))

    expected = next_stage(state)
    if expected is None:
        return False, ("The %s flow is complete: %s all passed. Report what they found rather than "
                       "running more of them." % (flow, ", ".join(done(state))))
    if stage != expected:
        return False, ("The next stage is %s, not %s. The %s flow runs %s in that order, and each "
                       "one is written to consume what the one before it established."
                       % (expected, stage, flow, ", ".join(known)))
    live = [e for e in state.get("stages", [])
            if e.get("stage") == stage and e.get("verdict") is None and not _silent(e)]
    if live and all(e.get("reopened") for e in live):
        # A refused stage is held open because its subagent carries on, but it may instead give up.
        # The session relaunching the stage is the sign that it did, and superseding the held-open
        # entry here is what keeps that from deadlocking the flow.
        state["stages"] = [e for e in state.get("stages", []) if e not in live]
    elif live:
        # This once dropped the live entry and admitted the relaunch, on the reasoning that tool
        # calls are sequential so the earlier launch must be stale. They are not: a stage is
        # launched as a task and the session goes on issuing calls while it runs. So the relaunch
        # was a real second worker on the same stage, and when one of the two reported, its verdict
        # closed the entry belonging to the other. That left nothing in flight while a subagent was
        # still working, and the flow guard duly told it it was the orchestrator.
        return False, ("The %s stage is already running. Wait for it to report -- read its output "
                       "rather than starting a second one, which would do the same work against "
                       "the same files and leave the gate unable to tell whose answer it judged."
                       % stage)
    return True, ""


def forget_running(state: dict, every: bool = False) -> list[str]:
    """Drop stages whose subagent went away, returning their names.

    This used to drop every stage in flight, on the reasoning that a parent cannot be finishing its
    turn while one of its own Task calls is outstanding. It can: a stage is launched as a task and
    the session goes on without it, so the parent reaches its stop routinely while the stage is
    still reading. Deleting the entry then loses a stage that was working perfectly well, and the
    flow forgets a run it is about to be told to repeat. Only a launch that has shown no sign of
    life for STALE_AFTER is treated as gone, which is the subagent that died without its stop ever
    firing.
    """
    gone = [e for e in state.get("stages", [])
            if e.get("verdict") is None and (every or _silent(e))]
    state["stages"] = [e for e in state.get("stages", []) if e not in gone]
    return [e["stage"] for e in gone]


def summary(state: dict) -> str:
    """One line per stage, for a human watching."""
    if not state.get("flow"):
        return "no flow running"
    marks = {"accepted": "ok", "refused": "refused", None: "running"}
    give_up = [s.name for s in cc_flow.flow_for(state["flow"]) or []
               if exhausted(state, s.name) and s.name not in done(state)]
    rows = ["%s flow: %s" % (state["flow"], state.get("task", "")[:60])]
    for entry in state.get("stages", []):
        spent = entry.get("finished", time.time()) - entry.get("launched", time.time())
        rows.append("  %-10s %-8s %4.0fs %s"
                    % (entry["stage"], marks[entry.get("verdict")], spent,
                       (" ".join(entry.get("gaps", []))[:70] if entry.get("gaps") else "")))
    left = next_stage(state)
    if give_up:
        rows.append("  given up on: %s (refused %d times)" % (", ".join(give_up), ROUND_CAP))
    rows.append("  next: %s" % (left or ("nothing, the flow is over" if give_up
                                         else "nothing, the flow is complete")))
    return "\n".join(rows)


def session_of(payload: dict) -> str:
    return payload.get("session_id") or os.environ.get("CLAUDE_SESSION_ID") or "unknown"


atexit.register(_unlock)   # a hook that read and decided to change nothing still lets go
