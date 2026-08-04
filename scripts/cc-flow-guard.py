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
import time
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


# The subagent types this client will accept. Anything else is refused by the client itself, after
# the hook has already committed the launch to the flow.
AGENT_TYPES = ("general-purpose", "Explore", "Plan", "claude", "statusline-setup")


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
    # The stage name belongs in the prompt, not here. A session put it here instead -- reasonably
    # enough -- and the client answered "Agent type 'survey' not found", by which time this hook had
    # already recorded the launch. The flow then held a stage open that did not exist and refused
    # every retry as a duplicate, until the context ran out.
    if merged.get("subagent_type") not in AGENT_TYPES:
        merged["subagent_type"] = "general-purpose"
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "permissionDecisionReason": reason,
        "updatedInput": merged}}))
    sys.exit(0)


def compose(stage, flow: str, task: str, prior: list[str], refused: str = "",
            gaps: list[str] = ()) -> str:
    """What a stage subagent is told: its stance, the task, and what the stages before it found.

    A refused round is not a fresh start. The round that follows it is a new subagent with no
    memory, and without the ledger it is meant to be fixing it does the work again from nothing:
    run 7's first claims round spent 252 tool calls and produced seven cited findings, two of them
    short of what the gate wanted, and the round after it made 16 calls and cited nothing at all.
    Handing the ledger back with the gaps marked is the difference between a correction and a
    retry.
    """
    parts = ["STAGE: %s" % stage.name,
             "You are one stage of a %s flow. Do this stage only." % flow,
             "",
             stage.stance,
             "",
             "TASK: %s" % task]
    if prior:
        parts += ["", "What the stages before you established, which you may build on but must "
                      "verify before you cite:", ""] + prior
    if refused:
        parts += ["",
                  "A previous round of this stage produced the ledger below and the gate refused "
                  "it. This is your ledger to correct, not a draft to replace: keep every claim it "
                  "does not object to, word for word, and spend your effort on the ones it names. "
                  "The work behind the accepted claims is done and paying for it twice is what "
                  "runs the budget out.", "",
                  "--- the refused ledger ---", refused, "--- end of it ---"]
        if gaps:
            parts += ["", "What the gate said was wrong with it:", ""]
            parts += ["  %d. %s" % (i, " ".join(g.split())) for i, g in enumerate(gaps, 1)]
    parts += ["",
              "Answer in the ledger format the session contract describes. Your answer is read by a "
              "gate that checks every claim against what you actually ran and read, so cite as you "
              "go rather than reconstructing citations at the end.",
              "",
              "Your report is the message you finish with, in full. Nothing you write to a file is "
              "read here: a stage that put its ledger in claims.jsonl and summarised it in prose "
              "had four findings judged as citing nothing, with the citations sitting in the file.",
              "",
              "Keep it under three hundred lines. The transport cuts a reply at 16,384 tokens and "
              "the cut takes the end, which is where the conclusions are: one survey here ran to "
              "16,384 tokens, arrived truncated, and cost the stage after it two minutes of "
              "reading. Quote the lines that carry a finding, not the file around them."]
    return "\n".join(parts)


# What the session may do while it is orchestrating rather than working. Bookkeeping and talking to
# the user are not the work; reading the code is.
EDITS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

CLERICAL = {"TodoWrite", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "TaskOutput",
            "AskUserQuestion", "ExitPlanMode", "SlashCommand"}


def _orchestrator_only(state: dict, tool: str, session: str = "", root: str = "",
                       tool_input: dict | None = None, agent: str = "") -> None:
    """While a flow is running with no stage in flight, the session is an orchestrator.

    Told to run its stages as subagents, the first real session read the file itself and answered
    from that -- politely, plausibly, and with none of the three stances applied. Instruction was
    not enough, which is the same lesson as every other rule here, so the tool call that does the
    work is refused until the stage that should be doing it has been launched.
    """
    if tool in CLERICAL:
        allow()
    if tool == "TaskStop" and cc_flowstate.deaf(state):
        # Normally killing a stage is the session avoiding the work. Not here: this one has been
        # told to stop and answer more times than any working stage ever needs, and the flow has no
        # other way to end it. The round is written off as it goes, so what it did establish is
        # salvaged and the stage counts as having had its turn.
        for stage_name in cc_flowstate.deaf(state):
            cc_flowstate.record_verdict(
                state, stage_name,
                ["the stage went on calling tools through %d refusals and never answered, so the "
                 "round was ended from outside" % cc_flowstate.DEAF_AFTER], "", reopen=False)
        cc_flowstate.save(state, session, root)
        allow()
    if tool == "TaskStop" and cc_flowstate.running(state):
        # Refused from doing the work itself, a session decided the guard was a sandbox, killed the
        # stage it had just launched on the grounds that it looked stuck, and went back to doing the
        # work itself. Waiting is what it is for; killing the stage is not clerical.
        # Told only to wait, a session alternated TaskStop with TaskOutput four times in nine
        # minutes, each refusal costing a full turn. So the refusal now names the call to make.
        deny("The %s stage is running. Do not call TaskStop again -- it will be refused every time "
             "until the stage reports, and each attempt costs you a turn. Call TaskOutput with "
             "task_id \"%s\", block true and timeout 600000, and wait there. A stage that has not "
             "answered yet is working, not stuck, and stopping it leaves the flow with nothing to "
             "judge." % (", ".join(cc_flowstate.running(state)), (tool_input or {}).get("task_id") or ""))
    in_flight = cc_flowstate.running(state)
    if agent and not in_flight:
        # This call comes from a subagent, so a stage is working whatever the flow remembers. Run 19
        # lost the launch of its claims stage to a race between hooks and then read all 157 of the
        # stage's own tool calls as the orchestrator idling, refusing each one with an order to launch
        # the stage that was making them. A worker is never told to launch itself; the record is put
        # back instead, and the stage is charged for the calls it has made since.
        stage = cc_flowstate.next_stage(state)
        if stage is not None:
            cc_flowstate.record_launch(state, stage, agent=agent)
            cc_flowstate.save(state, session, root)
            in_flight = cc_flowstate.running(state)
        else:
            allow()
    if in_flight:
        # A stage is in flight, so this is that stage working -- but only up to a point. Reading is
        # charged against a budget because a claims stage once spent 387 calls re-reading the files
        # it was about to cite, announcing each time that it needed to re-read them first.
        spent = cc_flowstate.spend(state, in_flight[0])
        if agent:
            # Bind the stage to the worker that is actually making the calls. Launches are recorded
            # before the client has said which agent it started, so the id only becomes knowable when
            # that agent calls a tool, and it is what lets anything later match a stage to a worker.
            for entry in reversed(state.get("stages") or []):
                if entry.get("stage") == in_flight[0] and entry.get("verdict") is None:
                    entry["agent"] = entry.get("agent") or agent
                    break
        cc_flowstate.save(state, session, root)
        # A stage that reads and judges must not write. The scripted path checks the tree's
        # fingerprint after the fact; here there is no after, so the edit is refused as it is made.
        # Run 12's claims stage wrote six test files into the worktree it was reviewing.
        if tool in EDITS:
            stage = cc_flow.stage_in(state.get("flow") or "", in_flight[0])
            if stage is not None and not stage.writes:
                deny("The %s stage does not write. Leave the tree exactly as you found it and put "
                     "what you learned in your answer. A file you create is not evidence here: "
                     "nothing reads it, and the stage after you sees the tree, not your scratch."
                     % in_flight[0])
        judged = cc_flow.stage_in(state.get("flow") or "", in_flight[0])
        allowed = (judged.budget if judged is not None and judged.budget else
                   cc_flowstate.CALL_BUDGET)
        if os.environ.get("CC_FLOW_TRACE"):
            # What a hook decided is otherwise unknowable: the client logs nothing, and a refusal
            # that never reaches the model looks exactly like a hook that did not run. This is how
            # run 24's invisible budget was found.
            try:
                with open(os.environ["CC_FLOW_TRACE"] + ".guard", "a") as fh:
                    fh.write("%s tool=%s agent=%s flight=%s spent=%s allowed=%s\n"
                             % (time.strftime("%H:%M:%S"), tool, agent[:8], in_flight, spent,
                                allowed))
            except OSError:
                pass
        over = spent - allowed
        if over > 0:
            cc_flowstate.refused_once_more(state, in_flight[0])
            cc_flowstate.save(state, session, root)
            # The refusal has to get shorter as it repeats, because it is charged to the context it
            # is trying to protect. Run 18's second round spent 361 calls against a budget of 140 and
            # was refused for 220 of them; by the end the client had room for two tokens of output,
            # so the stage that had been told to answer could not, and the answer on record is the
            # proxy's note that it was cut off at 2 tokens.
            if over > 6:
                deny("Refused. Answer now, in CLAIM/EVIDENCE/QUOTE blocks.")
            deny("You have spent %d tool calls on the %s stage. Stop reading and write your answer "
                 "now from what you have already seen. Anything you could not establish is an "
                 "UNKNOWN, which is a complete answer here -- going round the files again is not."
                 % (spent, in_flight[0]))
        allow()
    nxt = cc_flowstate.next_stage(state)
    if nxt is None:
        allow()             # flow complete: the session is writing its summary
    # Said at length, this got read as a sandbox to be worked around: the session tried a script in
    # /tmp, then a bare python -c, then answered from memory. The instruction now comes first and
    # the reasoning after, because only the first sentence reliably survives.
    #
    # And after three of them, nothing at all but the order. A session took the paragraph as a
    # puzzle -- "I'm encountering a persistent interception mechanism ... let me try a workaround" --
    # and spent six turns on grep, python3 -c, a heredoc and a temp script, filling its window with
    # identical refusals until it announced it was hitting the token limit. Explanation is what it
    # was arguing with, so past a point it is not offered.
    state["balked"] = int(state.get("balked", 0)) + 1
    cc_flowstate.save(state, session, root)
    if state["balked"] > 3:
        deny("Refused. Call the Agent tool, prompt first line `STAGE: %s`. Nothing else is "
             "permitted and no other phrasing will work." % nxt)
    deny("Launch the %s stage now: call the Agent tool with a prompt whose first line is exactly "
         "`STAGE: %s`. Its stance is filled in for you, so the rest of the prompt hardly matters. "
         "This is not a sandbox to work around -- doing the reading here would answer without any "
         "of the stances the %s flow exists to apply, and the answer would reach nobody, because "
         "only what a stage reports is gated. Every other tool call will be refused exactly like "
         "this one, however you phrase it: the refusal is not about the path, the tool or the "
         "command, so grep, cat and python3 -c will each cost you a turn and return this text."
         % (nxt, nxt, state["flow"]))


# What the client says when the task a flow is waiting for no longer exists.
_NO_TASKS = re.compile(r"No tasks found|task[^\n]{0,40}not found", re.I)
_FINISHED = re.compile(r"<status>\s*(completed|failed|cancelled|error)\s*</status>", re.I)


def _gone(payload: dict, tool: str) -> int:
    """The client's own account of whether the stage in flight is still alive.

    A refusal reopens a stage, because a refused subagent usually goes round again. When it does not
    -- and in a headless session it often does not -- the flow held a stage open that had exited,
    the Stop hook ordered the parent to wait for it, and the parent answered `TaskList`, was told
    `No tasks found`, and said so; eleven times. Nothing else can see a subagent die, so what the
    client prints about it is taken as evidence.
    """
    answer = payload.get("tool_response")
    text = json.dumps(answer) if isinstance(answer, (dict, list)) else str(answer or "")
    if not (_NO_TASKS.search(text) or (tool == "TaskOutput" and _FINISHED.search(text))):
        allow()
    root = payload.get("cwd") or os.getcwd()
    session = cc_flowstate.session_of(payload)
    state = cc_flowstate.load(session, root)
    if state.get("flow") and cc_flowstate.running(state):
        cc_flowstate.forget_running(state, every=True)
        cc_flowstate.save(state, session, root)
    allow()


def _launched(payload: dict) -> int:
    """After the fact: did the launch this hook admitted actually start?

    PreToolUse runs before the call, so a launch is recorded on the strength of being permitted. One
    was then refused by the client -- the session had put the stage name in `subagent_type` -- and
    the flow held open a stage that did not exist, refusing every retry as a duplicate. A failed
    launch is only visible here, in the result.
    """
    tool = payload.get("tool_name") or ""
    if tool in ("TaskList", "TaskOutput"):
        return _gone(payload, tool)
    if tool not in ("Task", "Agent"):
        allow()
    answer = payload.get("tool_response")
    failed = False
    if isinstance(answer, dict):
        failed = bool(answer.get("is_error")) or "not found" in str(answer.get("error") or "")
    elif isinstance(answer, str):
        failed = "not found" in answer or answer.startswith("Error")
    if not failed:
        allow()
    root = payload.get("cwd") or os.getcwd()
    session = cc_flowstate.session_of(payload)
    state = cc_flowstate.load(session, root)
    if state.get("flow") and cc_flowstate.running(state):
        cc_flowstate.forget_running(state, every=True)
        cc_flowstate.save(state, session, root)
    allow()


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

    if payload.get("hook_event_name") == "PostToolUse":
        return _launched(payload)

    tool = payload.get("tool_name") or ""
    root = payload.get("cwd") or os.getcwd()
    session = cc_flowstate.session_of(payload)
    state = cc_flowstate.load(session, root)
    if not state.get("flow"):
        allow()             # no flow running: ordinary delegation is none of our business

    tool_input = payload.get("tool_input") or {}
    # Who is calling. Undocumented, and long assumed absent -- the note in this repo said no client
    # identity ever reaches a hook -- but the client does send it: `agent_id` is the subagent's id on
    # its own calls and absent on the parent's. Measured from a live run: 15 payloads carrying it,
    # matching the survey stage's 15 calls exactly, and none on the orchestrator's Read, Agent and
    # TaskOutput. It is the difference between a stage working and a session avoiding the work.
    agent = str(payload.get("agent_id") or "")
    if tool not in ("Task", "Agent"):
        return _orchestrator_only(state, tool, session, root, tool_input, agent)

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
    # The last refusal of this stage, if there is one: what this round is here to fix.
    hurt = [e for e in state.get("stages", [])
            if e.get("stage") == stage.name and e.get("verdict") == "refused"]
    last = hurt[-1] if hurt else {}
    return amend(tool_input, compose(stage, state["flow"], state.get("task", ""), prior,
                                     last.get("answer", ""), last.get("gaps", ())),
                 "stage %s of the %s flow" % (stage_name, state["flow"]))


if __name__ == "__main__":
    sys.exit(main())
