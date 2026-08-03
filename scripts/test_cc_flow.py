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
        kind: str = "general-purpose") -> tuple[str, str, dict]:
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": session,
               "cwd": root, "tool_input": {"prompt": prompt, "description": "a stage",
                                           "subagent_type": kind}}
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
        decision, _, _ = run("", root, tool="Read")
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
        cc_flowstate.record_launch(state, "survey")
        state["stages"][-1]["calls"] = cc_flowstate.CALL_BUDGET
        cc_flowstate.save(state, "s1", root)
        decision, why, _ = run("", root, tool="Read")
    assert decision == "deny", why
    assert "Stop reading and write your answer" in why, why


def test_a_stage_reading_within_its_budget_is_left_alone() -> None:
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey")
        cc_flowstate.save(state, "s1", root)
        decision, why, _ = run("", root, tool="Read")
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
        decision, why = _edit("writes", root)
        assert decision == "deny", why
        assert "does not write" in why


def test_a_stage_that_is_meant_to_write_still_may() -> None:
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("change", "q", "allowed", root)
        cc_flowstate.record_launch(state, "implement")
        cc_flowstate.save(state, "allowed", root)
        decision, why = _edit("allowed", root)
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

    done = subprocess.run(["zsh", str(HERE / "claude-gemma.sh"), "--flows", "--print-settings"],
                          capture_output=True, text=True, timeout=120)
    printed = done.stdout[done.stdout.index("{"):]
    hooks = json.loads(printed)["hooks"]["PreToolUse"]
    assert "cc-flow-guard" in hooks[0]["hooks"][0]["command"], hooks


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
        first = _edit(session, root, tool="Read", path="a.py")
        seen = [_edit(session, root, tool="Read", path="a.py")[1] for _ in range(8)]
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
