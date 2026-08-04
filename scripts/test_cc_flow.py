"""The stage loop as a hook: what it admits, what it refuses, and what it tells a subagent."""

from __future__ import annotations

import json
import pathlib
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cc_flow          # noqa: E402
import cc_flowstate     # noqa: E402

GUARD = HERE / "cc-flow-guard.py"


def _load_guard():
    """The hook as a module, for the parts of it worth testing without a subprocess."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("flow_guard", GUARD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(prompt: str, root: str, session: str = "s1", tool: str = "Task",
        kind: str = "general-purpose", agent: str = "") -> tuple[str, str, dict]:
    """One call at the flow guard. `agent` is what tells it a stage is calling rather than the parent,
    and it is not optional for anything about a stage's own work: the guard charges a round for the
    calls its worker makes and deliberately not for the parent's."""
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": session,
               "cwd": root, "tool_input": {"prompt": prompt, "description": "a stage",
                                           "subagent_type": kind}}
    if agent:
        payload["agent_id"] = agent
    proc = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return "allow", "", {}
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    return (out.get("permissionDecision"), out.get("permissionDecisionReason", ""),
            out.get("updatedInput") or {})


def test_a_flow_that_is_not_running_is_none_of_the_hooks_business() -> None:
    with tempfile.TemporaryDirectory() as root:
        decision, _, _ = run("go and look at something", root)
    assert decision == "allow"


def test_the_first_stage_is_admitted_and_given_its_real_stance() -> None:
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("implement", "make the thing work", "s1", root)
        decision, _, amended = run("STAGE: plan\nhave a look I suppose", root)
        after = cc_flowstate.load("s1", root)
    assert decision == "allow", decision
    assert "Find the code the task names and stop" in amended["prompt"], amended
    assert "make the thing work" in amended["prompt"]
    assert cc_flowstate.running(after) == ["plan"], after
    assert state["flow"] == "implement"


def test_a_stage_out_of_order_is_refused_by_name() -> None:
    with tempfile.TemporaryDirectory() as root:
        cc_flowstate.begin("implement", "t", "s1", root)
        decision, why, _ = run("STAGE: implement\nlet me just crack on", root)
    assert decision == "deny"
    assert "next stage is plan" in why, why


def test_nothing_runs_after_a_blocking_stage_was_refused() -> None:
    """The whole reason this hook exists: a plan that committed to nothing was implemented anyway,
    faithfully, by the two stages below it."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("implement", "t", "s1", root)
        cc_flowstate.record_launch(state, "plan")
        cc_flowstate.record_verdict(state, "plan", ["This plan commits to nothing."])
        cc_flowstate.save(state, "s1", root)
        decision, why, _ = run("STAGE: implement\ncarrying on", root)
    assert decision == "deny"
    assert "was refused" in why and "commits to nothing" in why, why


def test_a_refused_stage_may_be_run_again() -> None:
    """Refusing the flow is not refusing the stage: the way out is to satisfy it."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("implement", "t", "s1", root)
        cc_flowstate.record_launch(state, "plan")
        cc_flowstate.record_verdict(state, "plan", ["This plan commits to nothing."])
        cc_flowstate.save(state, "s1", root)
        decision, why, _ = run("STAGE: plan\nthis time with a prediction", root)
    assert decision == "allow", why


def test_a_stage_the_flow_does_not_have_is_refused_with_the_list() -> None:
    with tempfile.TemporaryDirectory() as root:
        cc_flowstate.begin("review", "t", "s1", root)
        decision, why, _ = run("STAGE: implement\n", root)
    assert decision == "deny"
    assert "survey, claims, adversary" in why, why


def test_an_unlabelled_subagent_is_told_the_exact_line_to_write() -> None:
    with tempfile.TemporaryDirectory() as root:
        cc_flowstate.begin("review", "t", "s1", root)
        decision, why, _ = run("go and survey the repository", root)
    assert decision == "deny"
    assert "STAGE: survey" in why, why


def test_the_second_stage_is_given_what_the_first_found() -> None:
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey")
        cc_flowstate.record_verdict(state, "survey", [])
        state["stages"][-1]["summary"] = "the parser lives in cc_verify.py:200-260"
        cc_flowstate.save(state, "s1", root)
        decision, _, amended = run("STAGE: claims\n", root)
    assert decision == "allow"
    assert "cc_verify.py:200-260" in amended["prompt"], amended


def test_a_finished_flow_stops_being_a_flow() -> None:
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        for name in ("survey", "claims", "adversary"):
            cc_flowstate.record_launch(state, name)
            cc_flowstate.record_verdict(state, name, [])
        cc_flowstate.save(state, "s1", root)
        decision, why, _ = run("STAGE: claims\nonce more", root)
    assert decision == "deny"
    assert "complete" in why, why


def test_the_summary_reads_as_progress() -> None:
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("implement", "thread holdings through", "s1", root)
        cc_flowstate.record_launch(state, "plan")
        cc_flowstate.record_verdict(state, "plan", [])
        cc_flowstate.record_launch(state, "implement")
        text = cc_flowstate.summary(state)
    assert "plan" in text and "ok" in text
    assert "implement" in text and "running" in text
    assert "next: implement" in text, text


def test_both_flows_name_stages_that_exist() -> None:
    for flow, stages in cc_flow.FLOWS.items():
        for stage in stages:
            assert cc_flow.stage_in(flow, stage.name) is stage
            assert cc_flow.adapter_for(flow, stage.name)


CONTRACT = HERE / "cc-depth-contract.py"


def _prompt(text: str, root: str, session: str = "s1") -> dict:
    payload = {"hook_event_name": "UserPromptSubmit", "session_id": session, "cwd": root,
               "prompt": text}
    proc = subprocess.run([sys.executable, str(CONTRACT), "--adapter", "review"],
                          input=json.dumps(payload), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def test_typing_implement_starts_the_change_flow() -> None:
    """A person does not know at launch whether the next hour is a review or a change."""
    with tempfile.TemporaryDirectory() as root:
        out = _prompt("implement: thread holdings through the order path", root)
        state = cc_flowstate.load("s1", root)
    assert state["flow"] == "implement", state
    assert state["task"] == "thread holdings through the order path", state
    said = out["hookSpecificOutput"]["additionalContext"]
    assert "plan, implement, verify" in said, said
    assert "STAGE: <name>" in said, said


def test_typing_review_starts_the_review_flow_and_switches_the_contract() -> None:
    with tempfile.TemporaryDirectory() as root:
        _prompt("implement: something", root)
        out = _prompt("review: the trade execution path", root)
        state = cc_flowstate.load("s1", root)
    assert state["flow"] == "review", state
    assert "survey, claims, adversary" in out["hookSpecificOutput"]["additionalContext"]


def test_an_ordinary_prompt_starts_nothing() -> None:
    """Guessing from the shape of a sentence would mean sometimes silently launching three
    subagents and sometimes not."""
    with tempfile.TemporaryDirectory() as root:
        _prompt("could you review this function for me", root)
        assert cc_flowstate.load("s1", root).get("flow") in (None, ""), "started a flow"


def test_the_slash_form_starts_one_too() -> None:
    with tempfile.TemporaryDirectory() as root:
        _prompt("/implement fix the stale holdings read", root)
        assert cc_flowstate.load("s1", root)["flow"] == "implement"


def test_the_orchestrator_may_not_do_the_stage_s_work() -> None:
    """Told to run its stages as subagents, the first real session read the file itself and
    answered from that -- politely, plausibly, with none of the three stances applied."""
    with tempfile.TemporaryDirectory() as root:
        cc_flowstate.begin("review", "t", "s1", root)
        decision, why, _ = run("", root, tool="Read")
    assert decision == "deny", why
    assert "STAGE: survey" in why, why


def test_a_stage_in_flight_may_read_whatever_it_likes() -> None:
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey")
        cc_flowstate.save(state, "s1", root)
        decision, _, _ = run("", root, tool="Read", agent="a1")
    assert decision == "allow"


def test_bookkeeping_is_not_the_work() -> None:
    with tempfile.TemporaryDirectory() as root:
        cc_flowstate.begin("review", "t", "s1", root)
        assert run("", root, tool="TodoWrite")[0] == "allow"
        assert run("", root, tool="TaskUpdate")[0] == "allow"


def test_when_the_flow_is_done_the_session_may_write_its_summary() -> None:
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        for name in ("survey", "claims", "adversary"):
            cc_flowstate.record_launch(state, name)
            cc_flowstate.record_verdict(state, name, [])
        cc_flowstate.save(state, "s1", root)
        assert run("", root, tool="Read")[0] == "allow"


GATE = HERE / "cc-depth-gate.py"


def _subagent_stop(answer: str, root: str, session: str = "s1") -> str:
    payload = {"hook_event_name": "SubagentStop", "session_id": session, "cwd": root,
               "agent_id": "a1", "last_assistant_message": answer,
               "agent_transcript_path": ""}
    # The stop hook waits for a stage in flight, which is the point of it; a test that wanted the
    # full ninety seconds of that would be a test nobody runs.
    env = {**os.environ, "CC_FLOW_WAIT": os.environ.get("CC_FLOW_WAIT", "4")}
    proc = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=60, env=env)
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return "allow"
    return json.loads(proc.stdout).get("decision", "allow")


def test_a_survey_is_not_refused_for_making_no_claims() -> None:
    """It is an inventory, and making none is what it was told to do.

    Measured on the first flow that ran: refused at 59 seconds, for obeying its stance.
    """
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey")
        cc_flowstate.save(state, "s1", root)
        decision = _subagent_stop("The guard lives in scripts/cc-context-guard.py:1-200. "
                                  "The sleep rule is at line 210.", root)
        after = cc_flowstate.load("s1", root)
    assert decision == "allow", decision
    assert cc_flowstate.done(after) == ["survey"], after
    assert after["stages"][0]["summary"], "the next stage gets nothing"


def _stop(answer: str, root: str, session: str = "s1", resumed: bool = False) -> tuple[str, str]:
    payload = {"hook_event_name": "Stop", "session_id": session, "cwd": root,
               "last_assistant_message": answer, "transcript_path": "",
               "stop_hook_active": resumed}
    # The stop hook waits for a stage in flight, which is the point of it; a test that wanted the
    # full ninety seconds of that would be a test nobody runs.
    env = {**os.environ, "CC_FLOW_WAIT": os.environ.get("CC_FLOW_WAIT", "4")}
    proc = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=60, env=env)
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return "allow", ""
    out = json.loads(proc.stdout)
    return out.get("decision", "allow"), out.get("reason", "")


def test_a_session_may_not_end_two_stances_short() -> None:
    """The first flow that ran did exactly this: the survey reported, the orchestrator relayed it,
    and the session ended with one stage's view of the question."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey")
        cc_flowstate.record_verdict(state, "survey", [])
        cc_flowstate.save(state, "s1", root)
        decision, why = _stop("Here is what the survey found.", root)
    assert decision == "block", decision
    assert "STAGE: claims" in why, why


def test_a_finished_flow_may_end() -> None:
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        for name in ("survey", "claims", "adversary"):
            cc_flowstate.record_launch(state, name)
            cc_flowstate.record_verdict(state, name, [])
        state["handed"] = True      # the findings have already been given to the closing turn
        cc_flowstate.save(state, "s1", root)
        decision, _ = _stop("Here is what survived.", root)
    assert decision == "allow", decision


def test_stopping_while_a_stage_reads_is_told_to_wait_for_it() -> None:
    """A parent reaches its stop routinely with a stage still working: the launch returns a task
    and the turn ends while the reading goes on. This once deleted the entry and asked for a
    relaunch, so a stage that was working perfectly well was forgotten mid-run and a session that
    took the hint killed the task and answered from a file it had read itself."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey")
        cc_flowstate.save(state, "s1", root)
        decision, why = _stop("here is what I think", root)
        after = cc_flowstate.load("s1", root)
    assert decision == "block", decision
    assert "still running" in why, why
    assert cc_flowstate.running(after) == ["survey"], after


def test_a_stage_that_said_nothing_for_ten_minutes_is_given_up_on() -> None:
    """A subagent that dies never fires its stop, and its entry would hold the stage open for
    good -- the launch hook would refuse every relaunch as a duplicate."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey")
        state["stages"][-1]["launched"] -= cc_flowstate.STALE_AFTER + 1
        cc_flowstate.save(state, "s1", root)
        decision, why = _stop("here is what I think", root)
        after = cc_flowstate.load("s1", root)
    assert decision == "block", decision
    assert "STAGE: survey" in why, why
    assert cc_flowstate.running(after) == [], after


def test_a_relaunch_replaces_a_launch_that_died_without_reporting() -> None:
    """A subagent that vanishes never fires its stop, so its entry would hold the stage open for
    good and no relaunch could ever get in. Twice, in two live sessions, the session said "I need
    to wait" and stopped. Only an old launch counts as dead: a recent one is still working."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey")
        state["stages"][-1]["launched"] -= cc_flowstate.STALE_AFTER + 1
        cc_flowstate.save(state, "s1", root)
        decision, why, amended = run("STAGE: survey\n", root)
    assert decision == "allow", why
    assert "Map the territory" in amended["prompt"]


def test_the_pushing_is_bounded() -> None:
    """One push is too few for a three-stage flow; unbounded is a hang."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        state["nudges"] = 99
        cc_flowstate.save(state, "s1", root)
        decision, _ = _stop("I give up", root)
    assert decision == "allow", decision


def test_a_stage_reporting_refills_the_budget() -> None:
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        state["nudges"] = 4
        cc_flowstate.record_launch(state, "survey")
        cc_flowstate.save(state, "s1", root)
        _subagent_stop("survey found scripts/cc-context-guard.py:1-40", root)
        assert cc_flowstate.load("s1", root)["nudges"] == 0


def test_the_launch_keeps_the_arguments_it_came_with() -> None:
    """updatedInput replaces the input object rather than merging into it.

    Returning only the prompt deleted description and subagent_type, and every launch came back as
    "the required parameter description is missing" -- ten in one session, each read by the model
    as its own mistake, none of them its mistake.
    """
    with tempfile.TemporaryDirectory() as root:
        cc_flowstate.begin("review", "t", "s1", root)
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "session_id": "s1",
                   "cwd": root, "tool_input": {"prompt": "STAGE: survey\nhave a look",
                                               "description": "Survey the thing",
                                               "subagent_type": "general-purpose",
                                               "run_in_background": True}}
        proc = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=30)
        out = json.loads(proc.stdout)["hookSpecificOutput"]
    amended = out["updatedInput"]
    assert amended["description"] == "Survey the thing", amended
    assert amended["subagent_type"] == "general-purpose", amended
    assert "Map the territory" in amended["prompt"], amended
    # A backgrounded stage reports to nobody and the flow waits for a result that never comes.
    assert "run_in_background" not in amended, amended


def test_a_stage_that_answers_the_refusal_stops_being_refused() -> None:
    """A refusal sends the subagent round again rather than ending it, so one launch reports
    twice. Without this the later verdict lands nowhere and a blocking stage stays refused for
    good -- a flow that can never continue."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("implement", "t", "s1", root)
        cc_flowstate.record_launch(state, "plan", agent="a1")
        cc_flowstate.record_verdict(state, "plan", ["commits to nothing"], agent="a1")
        assert cc_flowstate.refused(state), state
        cc_flowstate.record_verdict(state, "plan", [], agent="a1")
    assert cc_flowstate.done(state) == ["plan"], state
    assert not cc_flowstate.running(state), state


def test_a_refused_stage_is_still_in_flight() -> None:
    """A refusal does not end the subagent. Closing the stage told the flow guard nothing was
    running, so the guard read the working stage as an idle orchestrator and ordered it to
    delegate its own work to a subagent -- which it did, reporting nothing, and was refused
    again. Five denials into that loop the stage gave up."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "claims", agent="a1")
        cc_flowstate.record_verdict(state, "claims", ["no claims were stated"], agent="a1")
        assert cc_flowstate.running(state) == ["claims"], state
        cc_flowstate.record_verdict(state, "claims", [], agent="a1")
        assert cc_flowstate.running(state) == [], state
    assert cc_flowstate.done(state) == ["claims"], state


def test_a_stage_already_running_is_not_launched_twice() -> None:
    """Dropping the live entry to admit a relaunch assumed tool calls are sequential. They are
    not: a stage is launched as a task and the session goes on issuing calls while it runs, so
    the relaunch was a real second worker on the same stage. When one reported, its verdict
    closed the other's entry, leaving nothing in flight while a subagent was still working."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey")
        ok, why = cc_flowstate.admits(state, "survey")
    assert not ok, state
    assert "already running" in why, why
    assert cc_flowstate.running(state) == ["survey"], state


def test_a_running_stage_may_not_be_killed() -> None:
    """Refused from doing the work itself, a session decided the guard was a sandbox, killed the
    stage it had just launched on the grounds that it looked stuck, and went back to doing the
    work itself -- a script in /tmp, then a bare python -c, then an answer from memory."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey")
        cc_flowstate.save(state, "s1", root)
        decision, why, _ = run("", root, tool="TaskStop")
    assert decision == "deny", why
    assert "working, not stuck" in why, why


def test_a_stage_that_will_not_stop_reading_is_made_to_answer() -> None:
    """A claims stage reached 387 tool calls, re-reading the files it was about to cite and
    announcing each time that it needed to re-read them first. Capping one answer's tokens does
    nothing about this: the loop is in the reading, not the writing."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "claims")   # the survey has a shorter leash of its own
        state["stages"][-1]["calls"] = cc_flowstate.CALL_BUDGET
        cc_flowstate.save(state, "s1", root)
        decision, why, _ = run("", root, tool="Read", agent="a1")
    assert decision == "deny", why
    assert "Stop reading and write your answer" in why, why


def test_a_stage_reading_within_its_budget_is_left_alone() -> None:
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey")
        cc_flowstate.save(state, "s1", root)
        decision, why, _ = run("", root, tool="Read", agent="a1")
        after = cc_flowstate.load("s1", root)
    assert decision == "allow", why
    assert after["stages"][-1]["calls"] == 1, after


def test_the_stop_hook_waits_rather_than_asking_the_session_to() -> None:
    """Told to wait for its survey, a session said "I'll wait for it to report" and stopped --
    eighty-four times, over ten minutes and seventy-six thousand output tokens. Blocking a stop
    cannot make a session wait; it can only make it speak again. So the hook waits, and when the
    stage reports while it is waiting the session is told what to do next instead."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey")
        cc_flowstate.save(state, "s1", root)

        def report() -> None:
            time.sleep(1.0)
            live = cc_flowstate.load("s1", root)
            cc_flowstate.record_verdict(live, "survey", [])
            cc_flowstate.save(live, "s1", root)

        threading.Thread(target=report, daemon=True).start()
        began = time.time()
        decision, why = _stop("here is what I think", root)
        waited = time.time() - began
    assert decision == "block", decision
    assert waited >= 1.0, waited
    assert "STAGE: claims" in why, why


def test_a_stage_is_told_that_its_report_is_the_message() -> None:
    """One wrote its ledger to claims.jsonl and finished with a prose summary. The gate reads the
    last message, so four well-cited findings were judged as citing nothing."""
    with tempfile.TemporaryDirectory() as root:
        cc_flowstate.save(cc_flowstate.begin("review", "t", "s1", root), "s1", root)
        _, _, amended = run("STAGE: survey\n", root)
    assert "Nothing you write to a file is read here" in amended["prompt"], amended["prompt"]


def test_a_stage_still_reading_is_not_given_up_on() -> None:
    """A claims round that had been going nine minutes was dropped and relaunched, because
    staleness counted from the launch alone. A stage that is reading is plainly alive."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey")
        state["stages"][-1]["launched"] -= cc_flowstate.STALE_AFTER * 2
        cc_flowstate.spend(state, "survey")
        ok, why = cc_flowstate.admits(state, "survey")
    assert not ok, state
    assert "already running" in why, why


def test_a_stage_still_reading_is_not_given_up_on() -> None:
    """A claims round that had been going nine minutes was dropped and relaunched, because
    staleness counted from the launch alone. A stage that is reading is plainly alive."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey")
        state["stages"][-1]["launched"] -= cc_flowstate.STALE_AFTER * 2
        cc_flowstate.spend(state, "survey")
        ok, why = cc_flowstate.admits(state, "survey")
    assert not ok, state
    assert "already running" in why, why


def test_a_launch_never_asks_for_a_subagent_type_the_client_lacks() -> None:
    """A session put the stage name in subagent_type. The client answered "Agent type 'survey' not
    found" -- after this hook had recorded the launch -- so the flow held open a stage that did not
    exist and refused every retry as a duplicate, until the context ran out."""
    with tempfile.TemporaryDirectory() as root:
        cc_flowstate.save(cc_flowstate.begin("review", "t", "s1", root), "s1", root)
        decision, _, amended = run("STAGE: survey\n", root, kind="survey")
    assert decision == "allow"
    assert amended["subagent_type"] == "general-purpose", amended


def test_a_launch_the_client_refused_does_not_hold_the_stage() -> None:
    """PreToolUse runs before the call, so a launch is recorded on the strength of being
    permitted. One was then refused by the client and the flow held open a stage that did not
    exist, refusing every retry as a duplicate until the context ran out. The result is the only
    place that failure is visible."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey")
        cc_flowstate.save(state, "s1", root)
        payload = {"hook_event_name": "PostToolUse", "tool_name": "Agent", "session_id": "s1",
                   "cwd": root, "tool_input": {},
                   "tool_response": "Agent type 'survey' not found. Available agents: ..."}
        proc = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        after = cc_flowstate.load("s1", root)
    assert cc_flowstate.running(after) == [], after
    assert cc_flowstate.next_stage(after) == "survey", after


def _post(tool: str, response, root: str, session: str = "s1") -> None:
    """Run the PostToolUse side of the flow guard, as the client would after a tool returned."""
    payload = {"hook_event_name": "PostToolUse", "tool_name": tool, "cwd": root,
               "session_id": session, "tool_response": response}
    proc = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr


def test_a_stage_the_client_says_is_gone_stops_blocking_the_flow() -> None:
    """A refusal reopens a stage, because a refused subagent usually goes round again. This one had
    exited, so the parent was ordered to wait for it, asked TaskList, was told `No tasks found`, and
    said so -- eleven times, until the nudge limit let the session go with nothing judged."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "claims", "a1")
        cc_flowstate.record_verdict(state, "claims", ["cites nothing"], "a1")
        cc_flowstate.save(state, "s1", root)
        assert cc_flowstate.running(cc_flowstate.load("s1", root)) == ["claims"]

        _post("TaskList", "No tasks found", root)
        assert not cc_flowstate.running(cc_flowstate.load("s1", root))


def test_a_stage_still_working_is_not_forgotten() -> None:
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "claims", "a1")
        cc_flowstate.save(state, "s1", root)
        _post("TaskOutput", "<status>running</status><output>reading</output>", root)
        assert cc_flowstate.running(cc_flowstate.load("s1", root)) == ["claims"]


def test_a_stage_refused_three_times_stops_being_asked() -> None:
    """Without a cap, claims was refused, relaunched and refused again until the nudge limit ended
    the session with nothing judged. Three rounds is the budget, and claims is blocking, so a
    review nobody could get claims out of ends rather than sending an adversary at nothing."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey", "a0")
        cc_flowstate.record_verdict(state, "survey", [], "a0")
        for _ in range(3):
            cc_flowstate.record_launch(state, "claims", "a1")
            cc_flowstate.record_verdict(state, "claims", ["cites nothing"], "a1",
                                        "CLAIM: something\n")
        assert cc_flowstate.exhausted(state, "claims")
        assert cc_flowstate.next_stage(state) is None, cc_flowstate.summary(state)


def test_rounds_that_never_wrote_a_ledger_are_counted_apart() -> None:
    """Two of run 7's three claims rounds ended mid-sentence without a ledger, so the stage was
    given up on having been judged on its evidence exactly once. A round that produced claims and a
    round that produced nothing are different failures and are budgeted separately."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        for _ in range(2):
            cc_flowstate.record_launch(state, "claims", "a1")
            cc_flowstate.record_verdict(state, "claims", ["Your turn ended before you answered"],
                                        "a1", "Now let me verify each of these.")
        assert cc_flowstate.exhausted(state, "claims"), "two silent rounds is the whole budget"

    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s2", root)
        cc_flowstate.record_launch(state, "claims", "a1")
        cc_flowstate.record_verdict(state, "claims", ["Your turn ended"], "a1", "about to start")
        for _ in range(2):
            cc_flowstate.record_launch(state, "claims", "a1")
            cc_flowstate.record_verdict(state, "claims", ["cites nothing"], "a1", "CLAIM: x\n")
        assert not cc_flowstate.exhausted(state, "s2" and "claims"), \
            "a silent round must not spend one of the three the evidence gets"


def test_a_blocking_stage_nobody_can_satisfy_ends_the_flow() -> None:
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("change", "t", "s1", root)
        first = cc_flowstate.next_stage(state)
        for _ in range(3):
            cc_flowstate.record_launch(state, first, "a1")
            cc_flowstate.record_verdict(state, first, ["no plan"], "a1")
        assert cc_flowstate.next_stage(state) is None, cc_flowstate.summary(state)


def test_a_session_that_keeps_working_around_the_guard_gets_the_short_version() -> None:
    """A session read the long refusal as a puzzle and spent six turns on grep, python3 -c, a
    heredoc and a temp script, filling its window with identical refusals. Explanation was what it
    was arguing with, so past three attempts it is not offered."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.save(state, "s1", root)
        said = ""
        for _ in range(5):
            decision, said, _ = run("", root, tool="Read")
            assert decision == "deny", said
        assert said.startswith("Refused."), said
        assert "sandbox" not in said, said


def test_the_wiring_routes_the_tools_the_hooks_have_rules_about() -> None:
    """Two fixes were dead for a run each because the matchers did not send them the tool. The flow
    guard reads TaskList and TaskOutput to notice a stage that has exited; the context guard refuses
    a ledger written with Write. Both were matched on other tools only."""
    root = Path(__file__).resolve().parent
    for name in ("flow_smoke.sh", "claude-gemma.sh"):
        text = (root / name).read_text()
        pre = [line for line in text.split("\n") if "matcher" in line and "Read|Bash" in line]
        post = [line for line in text.split("\n") if "matcher" in line and "Task|Agent" in line]
        assert pre and "Write" in pre[0], "%s: the context guard never sees a Write" % name
        assert post and "TaskList" in post[0], "%s: the flow guard never sees a TaskList" % name


def test_a_reopened_round_that_never_starts_is_given_up_on_quickly() -> None:
    """A refusal reopens the stage in case the subagent goes round again. Headless it exits instead,
    and the flow then held the stage open for ten minutes with nothing behind it while the parent was
    ordered to wait for a task the client had already forgotten."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "claims", "a1")
        cc_flowstate.record_verdict(state, "claims", ["cites nothing"], "a1")
        reopened = state["stages"][-1]
        assert reopened.get("reopened")
        reopened["launched"] = time.time() - (cc_flowstate.REOPENED_STALE + 1)
        assert cc_flowstate.forget_running(state) == ["claims"], state["stages"]

        # One that did start again is left alone: it is working, not gone.
        state = cc_flowstate.begin("review", "t", "s2", root)
        cc_flowstate.record_launch(state, "claims", "a2")
        cc_flowstate.record_verdict(state, "claims", ["cites nothing"], "a2")
        again = state["stages"][-1]
        again["launched"] = time.time() - (cc_flowstate.REOPENED_STALE + 1)
        again["calls"], again["active"] = 3, time.time()
        assert cc_flowstate.forget_running(state) == [], state["stages"]


def test_a_reopened_round_is_handed_the_ledger_it_must_fix() -> None:
    """Run 7's first claims round spent 252 tool calls and produced seven cited findings, two of
    them short. The round after it was a fresh subagent that had never seen them: 16 calls, six
    claims, not one citation. A refusal has to arrive with the thing being refused."""
    guard = _load_guard()
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("s1", "review", "review the guard", root)
        cc_flowstate.record_launch(state, "claims", "a1")
        cc_flowstate.record_verdict(state, "claims", ["claim 6 cites a command nothing ran"], "a1",
                                    "CLAIM: the switch can be made by another name\n"
                                    "QUOTE: g.py:12 `if OFF.exists():`\n")
        cc_flowstate.save(state, "s1", root)
        # The refusal reopens the stage, so the last entry is the new round and the ledger sits on
        # the one before it -- which is the entry the guard looks for too.
        hurt = [e for e in state["stages"] if e.get("verdict") == "refused"][-1]
        prompt = guard.compose(cc_flow.stage_in("review", "claims"), "review", "review the guard",
                               [], hurt.get("answer", ""), hurt.get("gaps", ()))
        assert "the switch can be made by another name" in prompt
        assert "claim 6 cites a command nothing ran" in prompt
        assert "word for word" in prompt


def test_an_accepted_stage_is_not_handed_a_ledger_to_fix() -> None:
    guard = _load_guard()
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("s2", "review", "review the guard", root)
        cc_flowstate.record_launch(state, "survey", "a1")
        cc_flowstate.record_verdict(state, "survey", [], "a1", "SURVEY: files")
        prompt = guard.compose(cc_flow.stage_in("review", "claims"), "review", "review the guard",
                               [], "", ())
        assert "refused ledger" not in prompt


def test_the_hand_back_survives_the_hook_and_not_just_the_function() -> None:
    """The hand-back is only worth anything if it reaches the subagent. Twice now a fix here was
    correct in the function and never reached, because the hook that calls it was not wired to the
    tool it needed to see. This drives the guard as the client does: a real PreToolUse payload for
    an Agent launch, and the amended prompt read back out of what the hook returned."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "review the guard", "s9", root)
        cc_flowstate.record_launch(state, "survey", "a0")
        cc_flowstate.record_verdict(state, "survey", [], "a0")
        cc_flowstate.record_launch(state, "claims", "a1")
        cc_flowstate.record_verdict(state, "claims", ["claim 6 cites a command nothing ran"], "a1",
                                    "CLAIM: the switch can be made under another name\n"
                                    "QUOTE: g.py:12 `if OFF.exists():`\n")
        # The refusal reopened the stage; the client's next launch is the round that must fix it.
        cc_flowstate.forget_running(state)
        cc_flowstate.save(state, "s9", root)

        payload = {"hook_event_name": "PreToolUse", "tool_name": "Agent", "cwd": root,
                   "session_id": "s9",
                   "tool_input": {"prompt": "STAGE: claims", "description": "claims",
                                  "subagent_type": "general-purpose"}}
        proc = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)["hookSpecificOutput"]
        assert out.get("permissionDecision") != "deny", out
        sent = (out.get("updatedInput") or {}).get("prompt", "")
        assert "the switch can be made under another name" in sent, sent[:400]
        assert "claim 6 cites a command nothing ran" in sent, sent[:400]


def _edit(session: str, root: str, tool: str = "Write", path: str = "probe.py",
          agent: str = "") -> tuple[str, str]:
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": session,
               "cwd": root, "tool_input": {"file_path": str(Path(root, path)), "content": "x = 1"}}
    if agent:
        payload["agent_id"] = agent
        payload["agent_type"] = "general-purpose"
    proc = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return "allow", ""
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    return out.get("permissionDecision", "allow"), out.get("permissionDecisionReason", "")


def test_a_stage_that_only_reads_cannot_write_to_the_tree_it_judges() -> None:
    """The scripted path takes the tree's fingerprint and compares it afterwards. Interactively
    there is no afterwards, so the edit is refused as it is made: run 12's claims stage wrote six
    test files into the worktree it was reviewing."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "q", "writes", root)
        cc_flowstate.record_launch(state, "claims")
        cc_flowstate.save(state, "writes", root)
        decision, why = _edit("writes", root, agent="a1")
        assert decision == "deny", why
        assert "does not write" in why


def test_a_stage_that_is_meant_to_write_still_may() -> None:
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("change", "q", "allowed", root)
        cc_flowstate.record_launch(state, "implement")
        cc_flowstate.save(state, "allowed", root)
        decision, why = _edit("allowed", root, agent="a1")
        assert decision != "deny", why


def test_the_budget_refusal_is_the_one_the_model_reads() -> None:
    """Behind the context guard the budget was invisible: run 12 spent 280 tool calls against a cap
    of 140 and never saw the message that says stop, because the hook ahead of it refused first.

    The launcher assembles its hooks at runtime, so this asks it what it would register rather than
    reading the source -- which is the difference that hid three fixes behind matchers that never
    routed the tool they were about.
    """
    settings = json.loads(pathlib.Path(HERE, "flow_smoke.sh").read_text()
                          .split("cat > \"$SETTINGS\" <<JSON")[1].split("\nJSON")[0]
                          .replace("$BASE", "b").replace("$MODEL", "m").replace("$CONTRACT", "c")
                          .replace("$GUARD", "context-guard").replace("$FLOW", "flow-guard")
                          .replace("$GATE", "g"))
    order = [h["hooks"][0]["command"] for h in settings["hooks"]["PreToolUse"]]
    assert order[0] == "flow-guard", order
    for event in ("Stop", "SubagentStop"):
        # Run 27's claims stage was still working when the default 600-second hook timeout killed
        # the 720-second wait. A timed-out Stop hook is ignored, so the parent exited successfully
        # and the client cancelled the live stage. The hook's own wait must fit inside this timeout.
        assert settings["hooks"][event][0]["hooks"][0]["timeout"] > 720, settings["hooks"][event]

    done = subprocess.run(["zsh", str(HERE / "claude-gemma.sh"), "--flows", "--print-settings"],
                          capture_output=True, text=True, timeout=120)
    printed = done.stdout[done.stdout.index("{"):]
    interactive = json.loads(printed)
    hooks = interactive["hooks"]["PreToolUse"]
    assert "cc-flow-guard" in hooks[0]["hooks"][0]["command"], hooks
    for event in ("Stop", "SubagentStop"):
        assert interactive["hooks"][event][0]["hooks"][0]["timeout"] > 720, \
            interactive["hooks"][event]


def test_the_budget_refusal_gets_shorter_as_it_repeats() -> None:
    """The refusal is charged to the context it protects. Run 18's second round was refused 220 times
    over budget, and by the end the client had two tokens of room for output: the stage that had been
    told to answer could not, and the answer on record is the proxy's note that it was cut at 2."""
    with tempfile.TemporaryDirectory() as root:
        session = "spent"
        state = cc_flowstate.begin("review", "q", session, root)
        cc_flowstate.record_launch(state, "claims")
        state["stages"][-1]["calls"] = cc_flowstate.CALL_BUDGET
        cc_flowstate.save(state, session, root)
        first = _edit(session, root, tool="Read", path="a.py", agent="a1")
        seen = [_edit(session, root, tool="Read", path="a.py", agent="a1")[1] for _ in range(8)]
        assert first[0] == "deny", first
        assert "You have spent" in first[1], first
        assert len(seen[-1]) < 80, seen[-1]
        assert "Answer now" in seen[-1], seen[-1]


def test_both_launchers_tell_the_client_how_big_the_window_is() -> None:
    """Unset, Claude Code assumes 200k, never compacts, and hands the server a prompt it must refuse.
    Run 18 died at 98,342 tokens against a window of 98,304 after 135 turns: 38 tokens over, and the
    refusal arrives as a 502 the client treats as fatal. The interactive launcher declared it; the
    headless one, where the long runs happen, did not."""
    for name in ("claude-gemma.sh", "flow_smoke.sh"):
        text = (pathlib.Path(__file__).resolve().parent / name).read_text()
        assert "CLAUDE_CODE_MAX_CONTEXT_TOKENS" in text, name
        assert "API_TIMEOUT_MS" in text, name


_RACER = """
import sys, time
sys.path.insert(0, %r)
import cc_flowstate
state = cc_flowstate.load("race", %r)
time.sleep(0.15)                     # the window in which an unserialised writer loses the others
state.setdefault("stages", []).append({"stage": "s" + sys.argv[1]})
cc_flowstate.save(state, "race", %r)
"""


def test_hooks_writing_at_once_do_not_lose_each_others_records() -> None:
    """A client that issues three tool calls in one turn runs three hooks at once. Unserialised, the
    slowest saves what it read before the others changed anything and their records are gone: run 19
    lost the launch of its claims stage that way, then read the stage's own 157 tool calls as the
    orchestrator idling and told the working subagent to launch itself, 77 times."""
    with tempfile.TemporaryDirectory() as root:
        here = str(pathlib.Path(__file__).resolve().parent)
        cc_flowstate.save({"flow": "review", "stages": []}, "race", root)
        src = _RACER % (here, root, root)
        running = [subprocess.Popen([sys.executable, "-c", src, str(i)]) for i in range(6)]
        for proc in running:
            proc.wait(timeout=60)
        kept = {e["stage"] for e in cc_flowstate.peek("race", root).get("stages", [])}
    assert kept == {"s%d" % i for i in range(6)}, kept


def test_a_subagents_call_is_never_answered_with_an_order_to_launch_itself() -> None:
    """The client sends `agent_id` on a subagent's calls and nothing on the parent's, so the two are
    distinguishable after all. Run 19 could not tell them apart, lost the launch record of its claims
    stage to a race, and refused all 157 of that stage's own calls with an order to launch it."""
    with tempfile.TemporaryDirectory() as root:
        session = "worker"
        state = cc_flowstate.begin("review", "q", session, root)
        cc_flowstate.save(state, session, root)         # nothing recorded as running
        decision, why = _edit(session, root, tool="Read", path="a.py", agent="ag123")
        assert decision == "allow", why
        after = cc_flowstate.peek(session, root)
        assert cc_flowstate.running(after) == ["survey"], after
        assert after["stages"][-1].get("agent") == "ag123", after["stages"][-1]


def test_the_parents_own_call_is_still_refused_while_a_stage_is_owed() -> None:
    with tempfile.TemporaryDirectory() as root:
        session = "parent"
        state = cc_flowstate.begin("review", "q", session, root)
        cc_flowstate.save(state, session, root)
        decision, why = _edit(session, root, tool="Read", path="a.py")
        assert decision == "deny", why
        assert "STAGE: survey" in why, why


def test_the_stage_on_record_learns_which_worker_is_making_its_calls() -> None:
    """A launch is recorded before the client has said which agent it started, so the id is only
    knowable when that agent calls a tool. Bound then, a stage can afterwards be matched to a worker
    rather than inferred from whatever happens to be in flight."""
    with tempfile.TemporaryDirectory() as root:
        session = "bind"
        state = cc_flowstate.begin("review", "q", session, root)
        cc_flowstate.record_launch(state, "survey")
        cc_flowstate.save(state, session, root)
        decision, why = _edit(session, root, tool="Read", path="a.py", agent="agworker")
        assert decision == "allow", why
        entry = cc_flowstate.peek(session, root)["stages"][-1]
        assert entry["agent"] == "agworker", entry


def test_a_stage_may_be_given_a_shorter_leash_than_the_flow() -> None:
    """An index of forty entries does not need what a claims stage needs. Run 20's survey spent 141
    calls, a third of them refused, and then wrote its index from what it had seen in the first
    twenty."""
    survey = cc_flow.stage_in("review", "survey")
    assert survey is not None and 0 < survey.budget < cc_flowstate.CALL_BUDGET, survey
    with tempfile.TemporaryDirectory() as root:
        session = "leash"
        state = cc_flowstate.begin("review", "q", session, root)
        cc_flowstate.record_launch(state, "survey")
        state["stages"][-1]["calls"] = survey.budget
        cc_flowstate.save(state, session, root)
        decision, why = _edit(session, root, tool="Read", path="a.py", agent="ag1")
        assert decision == "deny", why
        assert "survey" in why, why


# The worst characters-per-token ratio measured across twelve real transcripts against
# llama-server's own tokeniser. The client counts four to a token, so its estimate of a coding
# session can be this far low, and a declared ceiling is only safe if it survives being believed.
DENSEST_MEASURED = 2.76


def test_the_declared_window_survives_the_client_counting_tokens_as_prose() -> None:
    """Three quarters was not a margin, it was the cause of death.

    A declared ceiling is a promise the client keeps: it will not send past it. But it measures the
    promise in its own units, four characters to a token, and a session carrying source, JSON, diffs
    and command output runs 2.76 to 3.21 characters per token. So 98,304 declared is up to 142,000
    tokens offered to a 131,072-token window, and runs 18, 20 and 25 each died a few thousand tokens
    past the end while believing themselves inside budget. It was blamed on a 10% disagreement three
    times over; it is 45%.
    """
    text = (pathlib.Path(__file__).resolve().parent / "flow_smoke.sh").read_text()
    assert "DECLARED=$(( CTX * 65 / 100 ))" in text, "the margin is back to a rounding allowance"
    window = 131072
    declared = window * 65 // 100
    assert declared * (4 / DENSEST_MEASURED) < window, (
        "declaring %d tokens is %d as the runner counts them, which does not fit %d"
        % (declared, declared * 4 // DENSEST_MEASURED, window))


def test_the_widest_window_is_declared_with_the_margin_the_counts_need() -> None:
    """The same arithmetic where a person picks the window rather than the server reporting it."""
    done = subprocess.run(["zsh", str(HERE / "claude-gemma.sh"), "128k", "--print-settings"],
                          capture_output=True, text=True, timeout=180)
    printed = done.stdout[done.stdout.index("{"):]
    declared = int(json.loads(printed)["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"])
    assert declared * (4 / DENSEST_MEASURED) < 131072, (
        "declared %d, which is %d tokens to the runner and past the window it was given"
        % (declared, declared * 4 // DENSEST_MEASURED))


def test_findings_that_held_are_gathered_across_the_rounds_that_were_refused() -> None:
    """A refused round is not a worthless one. Run 21's claims stage was refused three times and
    proved six findings on the way, and the flow kept none of them because nothing looked below the
    verdict on the stage. Rounds restate what they already had, so the text deduplicates."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "claims", "a1")
        cc_flowstate.record_verdict(state, "claims", ["claim 3: unverified"], "a1", "ledger",
                                    [{"claim": "rm is not in _VERBS", "cites": ["g.py:229"]}])
        cc_flowstate.record_launch(state, "claims", "a1")
        cc_flowstate.record_verdict(state, "claims", ["claim 2: unverified"], "a1", "ledger",
                                    [{"claim": "rm is not in _VERBS", "cites": ["g.py:229-230"]},
                                     {"claim": "the probe is exempt", "cites": ["command: touch x"]}])
        kept = cc_flowstate.salvage(state, "claims")
        assert [f["claim"] for f in kept] == ["rm is not in _VERBS", "the probe is exempt"], kept
        assert kept[0]["cites"] == ["g.py:229-230"], "the later round's citation should win"
        assert cc_flowstate.salvage(state, "survey") == []


def test_a_stage_given_up_on_hands_over_what_it_proved() -> None:
    """The failure this fixes: run 21 verified six findings about a real rule, was refused a seventh
    time, and its final answer listed only what it had failed to establish -- a review that named no
    defect it had actually caught. The findings are now part of what the session is made to say."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey", "a0")
        cc_flowstate.record_verdict(state, "survey", [], "a0")
        for _ in range(3):
            cc_flowstate.record_launch(state, "claims", "a1")
            cc_flowstate.record_verdict(
                state, "claims", ["claim 7 (a seventh thing): unverified"], "a1", "ledger",
                [{"claim": "rm is not in the _VERBS regex", "cites": ["guard.py:229"]}])
        # Each refusal reopens the stage on the assumption the worker goes round again. Headless it
        # exits instead, and the entries age out by the time the session stops; without that the hook
        # is waiting for a ghost rather than answering.
        for entry in state["stages"]:
            if entry.get("verdict") is None:
                entry["launched"] = time.time() - cc_flowstate.STALE_AFTER - 5
        cc_flowstate.save(state, "s1", root)
        decision, reason = _stop("Here is the review.", root)
        assert decision == "block", reason
        assert "rm is not in the _VERBS regex" in reason, reason
        assert "guard.py:229" in reason, reason
        assert "abandoned" in reason, reason
        assert "a seventh thing" not in reason, "an unverified claim leaked into the safe answer"
        expected = cc_flowstate.load("s1", root)["final_answer"]
        decision, refused = _stop(expected + "\n\nClaim 7 might still be a bug.", root)
        assert decision == "block" and "exactly" in refused, refused
        decision, _ = _stop(expected, root)
        assert decision == "allow"


def test_a_finding_restated_is_kept_twice_rather_than_risk_losing_one() -> None:
    """Merging findings by how alike they read cannot be done safely. Measured on run 21's ledgers,
    a paraphrase of a finding scores 0.55-0.60 against its original while "blocks touch but not rm"
    scores 0.82 against "blocks rm but not touch" -- so every threshold that merges the restatements
    merges the opposites first. Only identical text is merged, and a duplicate in an answer is the
    price of never dropping a finding."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "claims", "a1")
        cc_flowstate.record_verdict(state, "claims", ["g"], "a1", "ledger", [
            {"claim": "The rule blocks touch but not rm.", "cites": ["g.py:229"]},
            {"claim": "The rule blocks rm but not touch.", "cites": ["g.py:230"]},
            {"claim": "The rule blocks touch but not rm.", "cites": ["g.py:229", "g.py:231"]},
        ])
        kept = cc_flowstate.salvage(state, "claims")
        assert len(kept) == 2, kept
        assert kept[0]["cites"] == ["g.py:229", "g.py:231"], "the better-cited wording wins"


def test_a_stage_the_flow_has_given_up_on_is_not_reopened() -> None:
    """A refusal reopens a stage so a late verdict from the same worker still lands. Past the round
    cap there is no later verdict anyone will read, and reopening spends what is left of the worker's
    budget on it: run 22's abandoned claims stage ran another 54 calls over five minutes while the
    session waited to write out the four findings that had survived."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "a task", "s1", root)
        for _ in range(cc_flowstate.ROUND_CAP):
            cc_flowstate.record_launch(state, "claims", "agent-1")
            cc_flowstate.record_verdict(state, "claims", ["claim 1 cites nothing"], agent="agent-1",
                                        answer="CLAIM: something\nEVIDENCE: nothing")
    assert cc_flowstate.exhausted(state, "claims")
    assert not cc_flowstate.running(state), cc_flowstate.running(state)
    assert not [e for e in state["stages"] if e.get("verdict") is None]


def test_a_stage_still_short_of_the_cap_is_reopened() -> None:
    """The reason the reopen exists in the first place has to keep working."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "a task", "s1", root)
        cc_flowstate.record_launch(state, "claims", "agent-1")
        cc_flowstate.record_verdict(state, "claims", ["claim 1 cites nothing"], agent="agent-1",
                                    answer="CLAIM: something\nEVIDENCE: nothing")
    assert not cc_flowstate.exhausted(state, "claims")
    assert cc_flowstate.running(state), state["stages"]


def test_a_closing_message_citing_a_file_that_is_not_there_is_sent_back() -> None:
    """Run 22 delivered four findings that had passed the gate beside three citations of files that
    do not exist -- `scripts/cc-context-guard`, then `scripts/cc`, then `scripts/c`, one path losing
    a character each time it was written. Everything before the closing message was checked; the
    closing message was not, so an hour of holding a stage to its evidence ended in invention."""
    with tempfile.TemporaryDirectory() as root:
        pathlib.Path(root, "scripts").mkdir()
        pathlib.Path(root, "scripts", "guard.py").write_text("_VERBS = 'touch'\n")
        state = cc_flowstate.begin("review", "t", "s1", root)
        for stage in ("survey", "claims", "adversary"):
            cc_flowstate.record_launch(state, stage, "a0")
            cc_flowstate.record_verdict(state, stage, [], "a0")
        state["handed"] = True      # this is about the message written after that hand-over
        cc_flowstate.save(state, "s1", root)
        decision, reason = _stop("The rule is at scripts/cc-context-guard line 229.", root)
        assert decision == "block", reason
        assert "scripts/cc-context-guard" in reason, reason
        # The real file, the switch it guards, and a URL are all left alone.
        good = "See scripts/guard.py, run `touch /tmp/cc-guard-off`, see https://example.com/a/b."
        decision, reason = _stop(good, root)
        assert decision != "block", reason


def test_the_closing_message_is_sent_back_a_bounded_number_of_times() -> None:
    """A session has to be able to end. Every finding in the message has already been judged by the
    time this runs, so a gate that will not let go is worse than one bad sentence."""
    with tempfile.TemporaryDirectory() as root:
        pathlib.Path(root, "scripts").mkdir()
        state = cc_flowstate.begin("review", "t", "s1", root)
        for stage in ("survey", "claims", "adversary"):
            cc_flowstate.record_launch(state, stage, "a0")
            cc_flowstate.record_verdict(state, stage, [], "a0")
        state["handed"] = True      # this is about the message written after that hand-over
        cc_flowstate.save(state, "s1", root)
        bad = "It is in scripts/nowhere.py, honestly."
        limit = _gate_limit()
        seen = [_stop(bad, root)[0] for _ in range(limit + 1)]
    assert seen[:limit] == ["block"] * limit, seen
    assert seen[-1] != "block", seen


def _gate_limit() -> int:
    """The gate is a hook script, run as a process by these tests, so its constants are read the
    same way rather than imported."""
    for line in open(GATE):
        if line.startswith("CLOSING_LIMIT"):
            return int(line.split("=")[1].split("#")[0])
    raise AssertionError("CLOSING_LIMIT is gone")


def test_a_stage_that_was_cut_off_is_not_accepted_as_its_own_map() -> None:
    """Run 23's survey hit the 16,384-token ceiling on its first turn and what reached the gate was
    the proxy's truncation note, alone. A survey makes no claims, so it is not verified, and `not
    verified` was reading as `accept whatever arrives`: the note was recorded as the map of the
    territory and the claims stage was launched to work from it."""
    cut = ("\n\n[This answer was cut off at 16384 tokens by the proxy, so what is above is "
           "incomplete and any tool call it was about to make was dropped. Do not repeat it from "
           "the start: write the short version now.]")
    with tempfile.TemporaryDirectory() as root:
        cc_flowstate.begin("review", "t", "s1", root)
        run("STAGE: survey\nhave a look", root)
        decision = _subagent_stop(cut, root)
        after = cc_flowstate.load("s1", root)
    assert decision == "block", decision
    assert not cc_flowstate.done(after), after
    assert [e for e in after["stages"] if e.get("verdict") == "refused"], after


def test_a_stage_that_works_on_through_refusals_can_be_ended() -> None:
    """No hook can stop a subagent: a refused call is a call that returns a refusal, and run 24's
    survey answered 220 of them by making another call. It was still going when the run was killed
    by hand half an hour later. The session that launched it can end it, and is told to."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey", "a1")
        for _ in range(cc_flowstate.DEAF_AFTER + 1):
            cc_flowstate.refused_once_more(state, "survey")
        cc_flowstate.save(state, "s1", root)
        assert cc_flowstate.deaf(state) == ["survey"], state
        decision, reason = _stop("waiting on the survey", root)
        assert decision == "block", reason
        assert "TaskStop" in reason, reason
        # And the call it is told to make is the one call normally refused while a stage runs.
        assert run("", root, tool="TaskStop")[0] == "allow"
        after = cc_flowstate.load("s1", root)
        assert not cc_flowstate.running(after), after
        assert [e for e in after["stages"] if e.get("verdict") == "refused"], after


def test_task_stop_is_still_refused_for_a_stage_that_is_working() -> None:
    """The ordinary case is a session killing a stage it has decided looks stuck, and going back to
    doing the work itself. One did."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey", "a1")
        cc_flowstate.save(state, "s1", root)
        assert run("", root, tool="TaskStop")[0] == "deny"


def test_the_index_stage_is_not_allowed_to_run_commands() -> None:
    """Run 24's survey was asked for a list of files and line ranges and went off to test the
    off-switch rule instead: 280 tool calls, half an hour, one shell command rewritten over and over
    against a refusal. The tool list it was handed said Read, Grep, Glob -- the client enforces none
    of that under --dangerously-skip-permissions, so the flow does."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey", "a1")
        cc_flowstate.save(state, "s1", root)
        decision, reason, _ = run("", root, tool="Bash", agent="a1")
        assert decision == "deny", reason
        assert "does not run commands" in reason, reason
        assert run("", root, tool="Grep", agent="a1")[0] == "allow"
        assert run("", root, tool="Read", agent="a1")[0] == "allow"


def test_a_stage_that_needs_a_shell_still_has_one() -> None:
    """The claims stage settles what a program does by running it, which is the whole point of it."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey", "a1")
        cc_flowstate.record_verdict(state, "survey", [], "a1")
        cc_flowstate.record_launch(state, "claims", "a2")
        cc_flowstate.save(state, "s1", root)
        assert run("", root, tool="Bash", agent="a2")[0] == "allow"


def _status_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "flowstatus", str(pathlib.Path(__file__).resolve().parent / "cc-flow-status.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_run_can_be_read_out_without_writing_python_at_a_shell() -> None:
    """Every post-mortem began by hand-writing the same twenty lines against flow.json, and the part
    a person wants -- what a given-up stage proved before it was given up on -- was in none of them."""
    status = _status_module()
    state = {"flow": "review", "stages": [
        {"stage": "survey", "verdict": "accepted", "calls": 13},
        {"stage": "claims", "verdict": "refused", "calls": 100,
         "gaps": ["claim 4 (the rule misses node) cites nothing. Quote the lines it rests on."],
         "stood": [{"claim": "the guard admits an off switch only when the launch allowed one",
                    "cites": ["scripts/cc-context-guard.py:323-324"]}]}]}
    said = status.findings(state)
    assert "proved: the guard admits an off switch" in said, said
    assert "scripts/cc-context-guard.py:323-324" in said, said
    assert "cites nothing" in said, said
    assert "survey: accepted, 13 tool calls" in said, said


def test_a_gap_is_read_out_whole() -> None:
    """A gap is a sentence, and a sentence cut at eighty columns is not one -- the summary line
    already truncates, which is why post-mortems went to the JSON instead."""
    status = _status_module()
    long_gap = "claim 1 " + "which the stage established by reading the file " * 6
    said = status.findings({"flow": "review", "stages": [
        {"stage": "claims", "verdict": "refused", "calls": 4, "gaps": [long_gap]}]})
    assert " ".join(said.split()).count("reading the file") == 6, said


def test_the_orchestrator_is_not_allowed_to_poll_the_stage() -> None:
    """Run 25 died of a full window: ten TaskOutput calls, each returning exactly 32,164 characters of
    the stage's working record, were 84% of the orchestrator's context -- and the last claims round was
    still working when the window ran out, so its findings were never collected."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "a review", "s-poll", root)
        cc_flowstate.record_launch(state, "claims", "agent1")
        cc_flowstate.save(state, "s-poll", root)
        payload = {"hook_event_name": "PreToolUse", "tool_name": "TaskOutput",
                   "session_id": "s-poll", "cwd": root, "tool_input": {"task_id": "abc"}}
        proc = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)["hookSpecificOutput"]
        said = out.get("permissionDecisionReason", "")
        assert out.get("permissionDecision") == "deny", out
        assert "Do not poll" in said, said
        assert "32,164" in said, "the refusal should say what a poll costs"


def test_the_wait_message_does_not_send_them_polling() -> None:
    """The Stop hook told the parent to poll, which is what filled the window."""
    gate = pathlib.Path(__file__).resolve().parent / "cc-depth-gate.py"
    assert "Do not call TaskOutput" in gate.read_text(), "the wait message still sells the poll"
    assert "TaskOutput tool for it with" not in gate.read_text()


def test_a_finished_flow_hands_its_proved_findings_to_the_closing_answer() -> None:
    """The parent never sees a finding: it launches stages, waits, and is told verdicts. The only way
    it ever got at them was to poll, which cost 32k characters a call and ended run 25."""
    import cc_flowstate
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "a review", "s-hand", root)
        for stage in ("survey", "claims", "adversary"):
            cc_flowstate.record_launch(state, stage, "a1")
            cc_flowstate.record_verdict(state, stage, [], "a1", answer="done", stood=[
                {"claim": "the %s rule misses node writes" % stage,
                 "cites": ["scripts/cc-context-guard.py:268-268"]}])
        cc_flowstate.save(state, "s-hand", root)
        decision, said = _stop("I reviewed it and it looks fine.", root, session="s-hand")
        assert decision == "block", said
        assert "the claims rule misses node writes" in said, said
        assert "scripts/cc-context-guard.py:268-268" in said, said


def test_a_finished_flow_that_proved_nothing_says_so_rather_than_inventing() -> None:
    import cc_flowstate
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "a review", "s-none", root)
        for stage in ("survey", "claims", "adversary"):
            cc_flowstate.record_launch(state, stage, "a1")
            cc_flowstate.record_verdict(state, stage, [], "a1", answer="done")
        cc_flowstate.save(state, "s-none", root)
        decision, said = _stop("Here is my review.", root, session="s-none")
        assert decision == "block", said
        assert "The review flow produced no verified findings." in said, said
        expected = cc_flowstate.load("s-none", root)["final_answer"]
        assert _stop(expected + " But I suspect a race.", root, session="s-none")[0] == "block"
        assert _stop(expected, root, session="s-none")[0] == "allow"


def test_the_parents_own_tool_calls_are_not_charged_to_the_stage_it_is_waiting_on() -> None:
    """A stage cannot be allowed to look alive because the parent is busy.

    Run 26's claims subagent had been finished for twelve minutes while the parent ran 206 Bash calls
    hunting for its output file on disk -- `TaskOutput` having been taken away, it reached around it --
    and every one of those calls refreshed the dead round's `active` timestamp. So the round was never
    silent, `forget_running` never dropped it, `admits` never let the flow move on, and the session sat
    there until it was killed from outside. The parent is never idle: being told to wait is precisely
    what makes it try something else.
    """
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "claims", "a1")
        state["stages"][-1]["calls"] = 3
        state["stages"][-1]["active"] = time.time() - 1000
        cc_flowstate.save(state, "s1", root)

        run("", root, tool="Read")                      # the parent, with no agent id
        idle = cc_flowstate.load("s1", root)["stages"][-1]
        assert idle["calls"] == 3, "the parent's call was charged to the stage: %s" % idle
        assert time.time() - idle["active"] > 900, (
            "the parent's call refreshed the stage's heartbeat, which is how run 26 hung")

        run("", root, tool="Read", agent="a1")          # the stage itself
        working = cc_flowstate.load("s1", root)["stages"][-1]
        assert working["calls"] == 4, "the stage's own call was not charged: %s" % working
        assert time.time() - working["active"] < 60, "the stage's own call must count as a heartbeat"


def test_a_round_the_parent_is_only_waiting_on_still_goes_stale() -> None:
    """The consequence of the above, at the level the flow actually decides on."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "claims", "a1")
        state["stages"][-1]["calls"] = 3
        state["stages"][-1]["active"] = time.time() - (cc_flowstate.STALE_AFTER + 60)
        state["stages"][-1]["launched"] = time.time() - (cc_flowstate.STALE_AFTER + 60)
        cc_flowstate.save(state, "s1", root)
        for _ in range(5):
            run("", root, tool="Read")                  # the parent, poking about
        after = cc_flowstate.load("s1", root)
        assert cc_flowstate.forget_running(after, every=False) == ["claims"], (
            "a round nothing but the parent has touched for %d seconds is not running"
            % cc_flowstate.STALE_AFTER)


def _parent_bash(session: str, root: str, command: str) -> tuple[str, str]:
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": session,
               "cwd": root, "tool_input": {"command": command, "description": "looking"}}
    proc = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return "allow", ""
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    return out.get("permissionDecision", "allow"), out.get("permissionDecisionReason", "")


def test_reaching_for_the_stages_working_file_on_disk_is_refused_too() -> None:
    """Taking TaskOutput away did not take away the want.

    Refused the tool, run 26's parent went after the same thing on disk and, not knowing where that
    file was, invented 206 paths -- session id and agent id mutating a character at a time, every one
    returning nothing, every one a turn. The loop rule could not see it because no two commands were
    alike, so the shape of what is being reached for is what gets matched.
    """
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "claims", "a1")
        cc_flowstate.save(state, "s1", root)
        for command in (
            "ls -la /private/tmp/claude-501/-private-tmp-r26tree/32280d93-b397/tasks/ab2e2d9f.output",
            "cat /private/tmp/claude-501/-privateBS4-r26tree/32280d93-h9g7/tasks/ab2e30811.output",
            "ls /tmp/x/tasks/agent-ab2e2d9f3301607c7.jsonl",
        ):
            decision, why = _parent_bash("s1", root, command)
            assert decision == "deny", (command, why)
            assert "waiting" in why, why


def test_the_parent_may_still_run_ordinary_commands_while_it_waits() -> None:
    """The rule is about one shape of reaching around, not about the shell."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "claims", "a1")
        cc_flowstate.save(state, "s1", root)
        for command in ("git status --short", "ls -la scripts/", "rg -n ROUND_CAP scripts/"):
            decision, why = _parent_bash("s1", root, command)
            assert "tasks/" not in why, (command, why)


def test_an_abandoned_flow_cannot_expand_unverified_claims_in_its_final_answer() -> None:
    """Run 28 got the verdict right and the deliverable wrong.

    Claims proved nothing and were abandoned, then the parent wrote two detailed "Unverified"
    findings anyway. One explanation was false and both carried invented line-numbered quotations.
    A warning label is not verification; with nothing proved, the only safe result is that fact.
    """
    import cc_flowstate
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "a review", "s-r28", root)
        cc_flowstate.record_launch(state, "survey", "survey-agent")
        cc_flowstate.record_verdict(state, "survey", [], "survey-agent")
        for round_ in range(cc_flowstate.ROUND_CAP):
            cc_flowstate.record_launch(state, "claims", "claims-agent")
            cc_flowstate.record_verdict(
                state, "claims", ["claim %d lacked its quoted lines" % round_],
                "claims-agent", answer="CLAIM: unsupported")
        for entry in state["stages"]:
            if entry.get("verdict") is None:
                entry["launched"] = time.time() - cc_flowstate.STALE_AFTER - 5
        cc_flowstate.save(state, "s-r28", root)

        decision, instruction = _stop("Here are two unverified findings.", root, session="s-r28")
        assert decision == "block", instruction
        expected = cc_flowstate.load("s-r28", root)["final_answer"]
        assert expected == ("The review flow produced no verified findings. "
                            "The claims stage was refused 3 times and abandoned.")

        leaked = (expected + "\n\n**Claim 1:** A stage can reopen. Unverified.\n"
                  "```\nif exhausted: clear_everything()\n```")
        decision, reason = _stop(leaked, root, session="s-r28")
        assert decision == "block", "post-gate analysis escaped into the deliverable"
        assert expected in reason and "exactly" in reason, reason
        assert _stop(expected, root, session="s-r28")[0] == "allow"


def test_a_parent_that_ignores_the_same_launch_order_is_stopped() -> None:
    """Run 29 alternated Write and Edit for more than 200 parent turns.

    Every Write got the same correct refusal; every Edit failed client-side because Write had been
    refused. Neither outcome can make the next repetition useful. Stages already have a deafness cap;
    the parent needs one too, and `continue: false` is the hook protocol's actual stop mechanism.
    """
    guard = _load_guard()
    with tempfile.TemporaryDirectory() as root:
        cc_flowstate.begin("review", "a review", "s-parent-deaf", root)
        last = None
        for _ in range(guard.PARENT_DEAF_AFTER):
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Write",
                       "session_id": "s-parent-deaf", "cwd": root,
                       "tool_input": {"file_path": "/tmp/test.txt", "content": "hello"}}
            proc = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload),
                                  capture_output=True, text=True, timeout=30)
            assert proc.returncode == 0, proc.stderr
            last = json.loads(proc.stdout)
        assert last and last.get("continue") is False, last
        assert "No final result was delivered" in last.get("stopReason", ""), last
        state = cc_flowstate.load("s-parent-deaf", root)
        assert state.get("aborted"), state
        assert state.get("final_answer") == state.get("aborted"), state


def test_launching_the_ordered_stage_resets_the_parent_refusal_streak() -> None:
    with tempfile.TemporaryDirectory() as root:
        cc_flowstate.begin("review", "a review", "s-reset", root)
        for _ in range(8):
            assert run("", root, session="s-reset", tool="Read")[0] == "deny"
        assert cc_flowstate.load("s-reset", root)["balked"] == 8
        assert run("STAGE: survey", root, session="s-reset")[0] == "allow"
        assert cc_flowstate.load("s-reset", root)["balked"] == 0


def test_an_aborted_flow_can_only_close_with_its_stored_safe_result() -> None:
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "a review", "s-aborted", root)
        expected = ("The review flow was aborted after the parent ignored 25 consecutive "
                    "instructions to launch the survey stage. No verified findings were delivered.")
        state["aborted"] = expected
        state["final_answer"] = expected
        cc_flowstate.save(state, "s-aborted", root)
        decision, reason = _stop("I found a likely race anyway.", root, session="s-aborted")
        assert decision == "block" and expected in reason, reason
        assert _stop(expected, root, session="s-aborted")[0] == "allow"


def test_parent_tools_are_bounded_while_a_stage_is_in_flight() -> None:
    """Run 30's worker was active while its parent spent dozens of turns polling with `sleep 120`.

    Parent calls must neither spend the worker's budget nor escape the parent's own deafness cap.
    """
    guard = _load_guard()
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "a review", "s-wait-deaf", root)
        cc_flowstate.record_launch(state, "claims", agent="worker")
        cc_flowstate.save(state, "s-wait-deaf", root)
        last = None
        for _ in range(guard.PARENT_DEAF_AFTER):
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                       "session_id": "s-wait-deaf", "cwd": root,
                       "tool_input": {"command": "sleep 120", "timeout": 130000}}
            proc = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload),
                                  capture_output=True, text=True, timeout=30)
            assert proc.returncode == 0, proc.stderr
            last = json.loads(proc.stdout)
        assert last and last.get("continue") is False, last
        after = cc_flowstate.load("s-wait-deaf", root)
        assert after["stages"][-1].get("calls", 0) == 0, after
        assert after.get("aborted"), after


def test_a_fresh_reopened_stage_is_not_blocked_by_the_parent_stop_wait() -> None:
    """Run 30's correction made six calls immediately after its parent's 12-minute wait ended.

    The wait itself prevented the refused worker from resuming. Return quickly but preserve the entry;
    deleting it as stale would let the parent launch a duplicate just as the original wakes up.
    """
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "a review", "s-reopened-wait", root)
        cc_flowstate.record_launch(state, "claims", agent="blocked")
        state["stages"][-1]["reopened"] = True
        cc_flowstate.save(state, "s-reopened-wait", root)
        started = time.time()
        decision, _ = _stop("waiting", root, session="s-reopened-wait")
        elapsed = time.time() - started
        after = cc_flowstate.load("s-reopened-wait", root)
        assert decision == "block"
        assert elapsed < 1.5, elapsed
        assert cc_flowstate.running(after) == ["claims"], after


def test_worker_progress_breaks_the_parent_refusal_streak() -> None:
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "a review", "s-progress-reset", root)
        cc_flowstate.record_launch(state, "claims", agent="worker")
        state["balked"] = 17
        cc_flowstate.save(state, "s-progress-reset", root)
        assert run("", root, session="s-progress-reset", tool="Read", agent="worker")[0] == "allow"
        after = cc_flowstate.load("s-progress-reset", root)
        assert after["balked"] == 0, after
        assert after["stages"][-1]["calls"] == 1, after


def test_a_refused_claims_worker_ends_and_a_fresh_one_gets_its_ledger() -> None:
    """Run 30 repaired twelve citations in a worker already at 82% context.

    Duplicate-read protection denied the source, so it reconstructed the quotes from memory and
    changed nearly every one. A fresh worker gets the ledger and gaps without the exhausted transcript.
    """
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "a review", "s-fresh-claims", root)
        cc_flowstate.record_launch(state, "survey", "survey-agent")
        cc_flowstate.record_verdict(state, "survey", [], "survey-agent")
        cc_flowstate.record_launch(state, "claims", "a1")
        cc_flowstate.save(state, "s-fresh-claims", root)
        answer = "CLAIM: a defect exists but this claim cites nothing"
        assert _subagent_stop(answer, root, session="s-fresh-claims") == "allow"
        after = cc_flowstate.load("s-fresh-claims", root)
        assert not cc_flowstate.running(after), after
        assert cc_flowstate.refused(after)[-1]["answer"] == answer, after
        decision, _, amended = run("STAGE: claims", root, session="s-fresh-claims")
        assert decision == "allow", decision
        assert "--- the refused ledger ---" in amended["prompt"], amended["prompt"]
        assert answer in amended["prompt"], amended["prompt"]
        assert "cites nothing" in amended["prompt"], amended["prompt"]
