"""The stage loop as a hook: what it admits, what it refuses, and what it tells a subagent."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cc_flow          # noqa: E402
import cc_flowstate     # noqa: E402

GUARD = HERE / "cc-flow-guard.py"


def run(prompt: str, root: str, session: str = "s1", tool: str = "Task") -> tuple[str, str, dict]:
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": session,
               "cwd": root, "tool_input": {"prompt": prompt, "description": "a stage"}}
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
    proc = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=60)
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
    proc = subprocess.run([sys.executable, str(GATE)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=60)
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


def test_a_stage_still_running_when_the_session_stops_went_away() -> None:
    """A parent cannot finish a turn while its own Task call is outstanding.

    Measured on the second flow: the orchestrator said it would wait for the survey, then answered
    from a file it had read itself while the survey was still going. Leaving the entry in flight
    would have the launch hook refuse the relaunch as a duplicate.
    """
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey")
        cc_flowstate.save(state, "s1", root)
        decision, why = _stop("here is what I think", root)
        after = cc_flowstate.load("s1", root)
    assert decision == "block", decision
    assert "STAGE: survey" in why, why
    assert cc_flowstate.running(after) == [], after


def test_a_relaunch_of_a_stage_that_never_reported_is_admitted() -> None:
    """Tool calls are sequential, so a launch arriving while one is outstanding means the earlier
    one never reported. Denying it as a duplicate leaves the session nowhere to go -- twice, in
    two live sessions, it said "I need to wait" and stopped."""
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "t", "s1", root)
        cc_flowstate.record_launch(state, "survey")
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
    assert state["stages"][-1]["rounds"] == 2, state
