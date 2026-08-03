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

import json
import os
import time
from pathlib import Path

import cc_flow
import cc_ledger


def path_for(session: str, root: str) -> Path:
    return cc_ledger.run_dir(session, root) / "flow.json"


def load(session: str, root: str) -> dict:
    try:
        return json.loads(path_for(session, root).read_text())
    except (OSError, ValueError):
        return {}


def save(state: dict, session: str, root: str) -> None:
    out = path_for(session, root)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(state, indent=2) + "\n")
    except OSError:
        pass


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


def _silent(entry: dict) -> bool:
    """Has this launch shown no sign of life for STALE_AFTER?

    Measured from the last tool call charged to it rather than from the launch, because a stage that
    is reading is plainly alive however long it has been at it. A round that had spent nine minutes
    was given up on and relaunched under the older rule, which counted from the launch alone.
    """
    last = max(float(entry.get("active") or 0), float(entry.get("launched") or 0))
    return time.time() - last >= STALE_AFTER


def record_launch(state: dict, stage: str, agent: str = "") -> dict:
    state.setdefault("stages", []).append(
        {"stage": stage, "agent": agent, "launched": time.time(), "verdict": None, "gaps": []})
    return state


def record_verdict(state: dict, stage: str, gaps: list, agent: str = "") -> dict:
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
        entry["rounds"] = int(entry.get("rounds", 1)) + (0 if pending else 1)
        if gaps:
            # A refusal is not the end of the subagent, so the stage is still in flight and must
            # still look it. Closing it here told the flow guard that nothing was running, and the
            # guard then read the working stage as an idle orchestrator and ordered it to delegate
            # its own work to a subagent. It obliged, reported nothing, and was refused again.
            record_launch(state, stage, entry.get("agent", ""))
            state["stages"][-1]["reopened"] = True
    return state


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


def running(state: dict) -> list[str]:
    return [e["stage"] for e in state.get("stages", []) if e.get("verdict") is None]


def next_stage(state: dict) -> str | None:
    """The stage that should run now, or None when the flow is complete."""
    stages = cc_flow.flow_for(state.get("flow", "")) or []
    finished = set(done(state))
    for stage in stages:
        if stage.name not in finished:
            return stage.name
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
    rows = ["%s flow: %s" % (state["flow"], state.get("task", "")[:60])]
    for entry in state.get("stages", []):
        spent = entry.get("finished", time.time()) - entry.get("launched", time.time())
        rows.append("  %-10s %-8s %4.0fs %s"
                    % (entry["stage"], marks[entry.get("verdict")], spent,
                       (" ".join(entry.get("gaps", []))[:70] if entry.get("gaps") else "")))
    left = next_stage(state)
    rows.append("  next: %s" % (left or "nothing, the flow is complete"))
    return "\n".join(rows)


def session_of(payload: dict) -> str:
    return payload.get("session_id") or os.environ.get("CLAUDE_SESSION_ID") or "unknown"
