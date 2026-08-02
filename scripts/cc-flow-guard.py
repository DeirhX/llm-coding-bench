#!/usr/bin/env python3
"""PreToolUse on Task: the stage loop, for a session where the loop is the conversation.

The scripted driver decides what runs next and refuses to continue past a refused plan. Run the same
flow as native subagents -- which is the only way to watch it work -- and that decision moves to the
model, which is the thing under test. So the decision moves here instead: a launch is admitted only
if it is the next stage of a flow that is running and nothing blocking has been refused.

It also does the mechanical part the model should not have to get right. A stage's stance is long,
particular, and identical every time, so the hook substitutes the real one rather than trusting a
paraphrase, and records which stage this agent is running so that SubagentStop can judge it under
the right contract.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cc_flow          # noqa: E402
import cc_flowstate     # noqa: E402

STAGE_LINE = re.compile(r"^\s*STAGE:\s*(?P<stage>[a-z-]+)\s*$", re.M | re.I)


def allow() -> None:
    sys.exit(0)


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason}}))
    sys.exit(0)


def amend(tool_input: dict, prompt: str, reason: str) -> None:
    """Let the call through with a prompt of our own, and everything else it came with.

    `updatedInput` replaces the whole input object rather than merging into it. Returning just the
    prompt therefore deleted `description` and `subagent_type`, and every launch came back as
    "the required parameter description is missing" -- ten of them in one session, each one read by
    the model as its own mistake, none of them its mistake.
    """
    merged = dict(tool_input)
    merged["prompt"] = prompt
    # A backgrounded stage reports to nobody and the flow waits for a result that never arrives.
    merged.pop("run_in_background", None)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "permissionDecisionReason": reason,
        "updatedInput": merged}}))
    sys.exit(0)


def compose(stage, flow: str, task: str, prior: list[str]) -> str:
    """What a stage subagent is told: its stance, the task, and what the stages before it found."""
    parts = ["STAGE: %s" % stage.name,
             "You are one stage of a %s flow. Do this stage only." % flow,
             "",
             stage.stance,
             "",
             "TASK: %s" % task]
    if prior:
        parts += ["", "What the stages before you established, which you may build on but must "
                      "verify before you cite:", ""] + prior
    parts += ["",
              "Answer in the ledger format the session contract describes. Your answer is read by a "
              "gate that checks every claim against what you actually ran and read, so cite as you "
              "go rather than reconstructing citations at the end."]
    return "\n".join(parts)


# What the session may do while it is orchestrating rather than working. Bookkeeping and talking to
# the user are not the work; reading the code is.
CLERICAL = {"TodoWrite", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TaskOutput",
            "TaskStop", "AskUserQuestion", "ExitPlanMode", "SlashCommand"}


def _orchestrator_only(state: dict, tool: str) -> None:
    """While a flow is running with no stage in flight, the session is an orchestrator.

    Told to run its stages as subagents, the first real session read the file itself and answered
    from that -- politely, plausibly, and with none of the three stances applied. Instruction was
    not enough, which is the same lesson as every other rule here, so the tool call that does the
    work is refused until the stage that should be doing it has been launched.
    """
    if tool in CLERICAL:
        allow()
    if cc_flowstate.running(state):
        allow()             # a stage is in flight, and this is that stage working
    nxt = cc_flowstate.next_stage(state)
    if nxt is None:
        allow()             # flow complete: the session is writing its summary
    deny("You are running a %s flow, and %s has not run yet. Do not do its work here: launch a "
         "subagent whose prompt begins with `STAGE: %s` and wait for it to report. Its stance is "
         "supplied for you. Reading and running here would answer the question without any of the "
         "stances the flow exists to apply, and the answer would not be gated."
         % (state["flow"], nxt, nxt))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        allow()

    if os.environ.get("CC_FLOW_TRACE"):
        # Whether a hook can tell the orchestrator's tool call from a stage's own is not documented
        # and matters a great deal, so it is measured rather than assumed.
        try:
            with open(os.environ["CC_FLOW_TRACE"], "a") as fh:
                fh.write(json.dumps({k: (v if not isinstance(v, (dict, list)) else "...")
                                     for k, v in payload.items()}) + "\n")
        except OSError:
            pass

    tool = payload.get("tool_name") or ""
    root = payload.get("cwd") or os.getcwd()
    session = cc_flowstate.session_of(payload)
    state = cc_flowstate.load(session, root)
    if not state.get("flow"):
        allow()             # no flow running: ordinary delegation is none of our business

    if tool not in ("Task", "Agent"):
        return _orchestrator_only(state, tool)

    tool_input = payload.get("tool_input") or {}
    prompt = str(tool_input.get("prompt") or "")
    found = STAGE_LINE.search(prompt)
    if not found:
        deny("A subagent in a %s flow must say which stage it is, as its first line, exactly: "
             "STAGE: %s. The stages run in that order and each is written to consume what the one "
             "before it established, so an unlabelled one cannot be judged or sequenced."
             % (state["flow"], cc_flowstate.next_stage(state) or "none, the flow is complete"))

    stage_name = found.group("stage").lower()
    ok, why = cc_flowstate.admits(state, stage_name)
    if not ok:
        deny(why)

    stage = cc_flow.stage_in(state["flow"], stage_name)
    prior = [e.get("summary", "") for e in state.get("stages", []) if e.get("summary")]
    cc_flowstate.record_launch(state, stage_name)
    cc_flowstate.save(state, session, root)

    # The model asked for the right stage; the wording of it is not its business. Substituting the
    # stance verbatim also keeps the interactive path and the scripted one on the same words, which
    # is the only reason a result from one says anything about the other.
    return amend(tool_input, compose(stage, state["flow"], state.get("task", ""), prior),
                 "stage %s of the %s flow" % (stage_name, state["flow"]))


if __name__ == "__main__":
    sys.exit(main())
