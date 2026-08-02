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


def record_launch(state: dict, stage: str, agent: str = "") -> dict:
    state.setdefault("stages", []).append(
        {"stage": stage, "agent": agent, "launched": time.time(), "verdict": None, "gaps": []})
    return state


def record_verdict(state: dict, stage: str, gaps: list, agent: str = "") -> dict:
    """Attach a gate verdict to the most recent launch of `stage`."""
    for entry in reversed(state.get("stages", [])):
        if entry.get("stage") == stage and entry.get("verdict") is None:
            entry["verdict"] = "refused" if gaps else "accepted"
            entry["gaps"] = list(gaps)
            entry["agent"] = agent or entry.get("agent", "")
            entry["finished"] = time.time()
            break
    return state


def done(state: dict) -> list[str]:
    return [e["stage"] for e in state.get("stages", []) if e.get("verdict") == "accepted"]


def refused(state: dict) -> list[dict]:
    return [e for e in state.get("stages", []) if e.get("verdict") == "refused"]


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
    if stage in running(state):
        return False, ("A %s stage is already running and has not reported yet. Wait for it."
                       % stage)
    return True, ""


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
