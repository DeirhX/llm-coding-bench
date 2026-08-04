"""What the context guard refuses, and how it says so.

The guard had no tests until run 24, which is how a rule that refused 256 calls in a row without
ever changing its wording went unnoticed for four runs. It is exercised here as the client exercises
it -- as a process, over stdin, reading its decision off stdout -- because the thing being checked is
the decision that reaches the model, not the return value of a function.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cc_flowstate  # noqa: E402

GUARD = str(Path(__file__).resolve().parent / "cc-context-guard.py")


def ask(tool: str, tool_input: dict, root: str, session: str = "s1", agent: str = "",
        args: list[str] | None = None, transcript: str = "") -> str:
    """The guard's decision on one call: the refusal it would send, or "" for allowed."""
    payload = {"hook_event_name": "PreToolUse", "session_id": session, "cwd": root,
               "tool_name": tool, "tool_input": tool_input,
               # A payload with no session and no transcript is a probe of the hook rather than a call
               # to it, and the guard keeps its counters out of those. Tests that are about a session
               # therefore have to look like one.
               "transcript_path": transcript or "/tmp/cc-test-transcript.jsonl"}
    if agent:
        payload["agent_id"] = agent
    proc = subprocess.run([sys.executable, GUARD] + (args or []), input=json.dumps(payload),
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-500:]
    if not proc.stdout.strip():
        return ""
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    return out["permissionDecisionReason"] if out.get("permissionDecision") == "deny" else ""


def _overspent(root: str, stage: str = "survey", calls: int = 280) -> None:
    state = cc_flowstate.begin("review", "task", "s1", root)
    cc_flowstate.record_launch(state, stage, "a1")
    for entry in state["stages"]:
        entry["calls"] = calls
    cc_flowstate.save(state, "s1", root)


def test_a_stage_past_its_budget_is_told_so_by_this_guard_too() -> None:
    """Only one PreToolUse refusal reaches the model when two hooks refuse the same call, and it is
    not the first one: run 24's flow guard denied 220 consecutive calls with `spent=280 allowed=60`
    in its own trace, while the survey read this guard's message about something else every time and
    never learned it was out of budget. Both hooks now say it, so which is heard does not matter."""
    with tempfile.TemporaryDirectory() as root:
        _overspent(root)
        said = ask("Bash", {"command": "rg -n tampers scripts/cc-context-guard.py"}, root)
    assert "Stop reading" in said, said
    assert "survey" in said, said


def test_a_stage_inside_its_budget_is_left_alone() -> None:
    with tempfile.TemporaryDirectory() as root:
        _overspent(root, calls=5)
        assert ask("Bash", {"command": "rg -n tampers scripts/cc-context-guard.py"}, root) == ""


def test_the_same_refusal_five_times_stops_being_the_same_refusal() -> None:
    """Run 24's survey was told the same thing about the off-switch 256 times and spent half an hour
    rewriting the command to slip past it -- one attempt was `'(''?'':''t''o''u''c''h''`. A rule that
    answers identically forever reads as an obstacle with a trick to it."""
    session = "repeats-%d" % os.getpid()
    ledger = Path("/tmp/cc-refusals-%s.json" % session)
    ledger.unlink(missing_ok=True)
    try:
        with tempfile.TemporaryDirectory() as root:
            said = [ask("Bash", {"command": "touch /tmp/cc-guard-off"}, root, session=session)
                    for _ in range(6)]
    finally:
        ledger.unlink(missing_ok=True)
    assert all(said), "every one of these must be refused"
    assert said[0] == said[1], "the first refusals are the useful ones and should not change"
    assert "for the 6th time" in said[-1], said[-1]
    assert "Stop and answer" in said[-1], said[-1]


def test_a_different_refusal_does_not_inherit_the_count() -> None:
    """Two rules tripped alternately are not one rule repeating."""
    session = "mixed-%d" % os.getpid()
    ledger = Path("/tmp/cc-refusals-%s.json" % session)
    ledger.unlink(missing_ok=True)
    try:
        with tempfile.TemporaryDirectory() as root:
            for _ in range(5):
                ask("Bash", {"command": "touch /tmp/cc-guard-off"}, root, session=session)
            other = ask("Bash", {"command": "sleep 600"}, root, session=session)
    finally:
        ledger.unlink(missing_ok=True)
    assert "Do not sleep" in other, other
    assert "time" not in other.split(".")[0], other


def test_the_off_switch_is_still_defended() -> None:
    with tempfile.TemporaryDirectory() as root:
        assert ask("Bash", {"command": "touch /tmp/cc-guard-off"}, root)
        assert ask("Bash", {"command": "python3 -c \"open('/tmp/cc-depth-off','w')\""}, root)


def test_a_file_whose_name_merely_ends_in_the_switch_is_not_the_switch() -> None:
    """The name was bounded on the right and not on the left, so `my-cc-guard-off` matched inside a
    longer name and a review probing this very rule was refused for a file it does not name."""
    with tempfile.TemporaryDirectory() as root:
        assert ask("Bash", {"command": "touch /tmp/my-cc-guard-off"}, root) == ""


def test_a_long_sleep_is_refused_and_a_short_one_is_not() -> None:
    with tempfile.TemporaryDirectory() as root:
        assert "Do not sleep" in ask("Bash", {"command": "sleep 180 && tail -5 log"}, root)
        assert ask("Bash", {"command": "sleep 2 && echo done"}, root) == ""



def _guard_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("ctxguard", GUARD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _session_tree(parent_lines: int, own_lines: int) -> tuple[str, str]:
    """A session transcript and one subagent's, so that the two can be told apart by size."""
    import pathlib as pl
    import tempfile
    home = pl.Path(tempfile.mkdtemp())
    parent = home / "session.jsonl"
    filler = "x" * 400

    def rows(count: int) -> str:
        return "\n".join(
            json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "%d %s" % (i, filler)}]}}) for i in range(count)) + "\n"

    parent.write_text(rows(parent_lines))
    mine = home / "session" / "subagents"
    mine.mkdir(parents=True)
    own = mine / "agent-deadbeef01234567.jsonl"
    own.write_text(rows(own_lines))
    return str(parent), str(own)


def test_a_subagent_is_charged_for_its_own_window_not_the_parents() -> None:
    """Run 25 refused two subagents at "99% of the 98,304-token window" a minute apart, the same
    figure to the token, while their own transcripts held 51,744 and 38,347 tokens: both were being
    charged for the orchestrator's window, the one thing in the run neither could affect."""
    guard = _guard_module()
    parent, own = _session_tree(parent_lines=400, own_lines=4)
    mine = guard.own_transcript({"transcript_path": parent, "agent_id": "deadbeef01234567"})
    assert str(mine) == own, mine
    theirs = guard.own_transcript({"transcript_path": parent})
    assert str(theirs) == parent, theirs


def test_the_client_naming_the_agents_transcript_is_believed() -> None:
    guard = _guard_module()
    parent, own = _session_tree(parent_lines=9, own_lines=2)
    got = guard.own_transcript({"transcript_path": parent, "agent_transcript_path": own,
                                "agent_id": "deadbeef01234567"})
    assert str(got) == own, got


def test_an_agent_with_no_transcript_of_its_own_falls_back_to_the_one_named() -> None:
    """Better to measure the wrong window than to measure nothing and let a session run itself out."""
    guard = _guard_module()
    parent, _ = _session_tree(parent_lines=5, own_lines=1)
    got = guard.own_transcript({"transcript_path": parent, "agent_id": "nosuchagentatall"})
    assert str(got) == parent, got


def test_a_full_parent_does_not_refuse_a_fresh_subagents_read() -> None:
    """The refusal that cost run 25 both its claims rounds: a subagent's read, denied for a window
    that belongs to somebody else."""
    with tempfile.TemporaryDirectory() as root:
        parent, own = _session_tree(parent_lines=900, own_lines=2)
        said = ask("Read", {"file_path": GUARD, "limit": 40}, root,
                   agent="deadbeef01234567", transcript=parent)
        assert "window" not in said, said
        theirs = ask("Read", {"file_path": GUARD, "limit": 40}, root, transcript=parent)
        assert "window" in theirs, "the parent's own fullness stopped being noticed"


def test_a_refusal_from_this_guard_counts_towards_giving_the_stage_up() -> None:
    """A stage that ignores refusals is ended from outside, and the count is how the run knows to do
    it. Run 25's survey was cornered by the off-switch rule, which lives here and counted nothing,
    and it went on calling tools for the rest of its round with nobody keeping score."""
    import cc_flowstate
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "a review", "s-deaf", root)
        cc_flowstate.record_launch(state, "survey", "agent1")
        cc_flowstate.save(state, "s-deaf", root)
        said = ask("Bash", {"command": "touch /tmp/cc-guard-off"}, root, session="s-deaf",
                   agent="agent1")
        assert "off-switch" in said, said
        after = cc_flowstate.peek("s-deaf", root)
        counted = [e.get("denied") for e in after["stages"] if e.get("stage") == "survey"]
        assert counted and counted[-1] == 1, after["stages"]


def test_a_payload_with_no_transcript_does_not_throw() -> None:
    """A hook that raises gives the client no decision at all, which is worse than a wrong one."""
    guard = _guard_module()
    assert str(guard.own_transcript({"agent_id": "deadbeef01234567"})) in (".", "")


def test_a_probe_of_the_hook_does_not_spend_the_sessions_counters() -> None:
    """Feeding the hook a payload is the only admissible evidence about the hook, and run 25's claims
    stage did it 65 times. What it must not do is move the accounting of the session it is asking
    about: the escalation for a session going in circles was being counted against a probe."""
    import cc_flowstate
    with tempfile.TemporaryDirectory() as root:
        state = cc_flowstate.begin("review", "a review", "s-probe", root)
        cc_flowstate.record_launch(state, "claims", "agent1")
        cc_flowstate.save(state, "s-probe", root)
        for _ in range(6):
            said = ask("Bash", {"command": "touch /tmp/cc-guard-off"}, root, session="",
                       transcript="none")
            assert "off-switch" in said, said
            assert "for the" not in said, "a probe was escalated at like a session"
        after = cc_flowstate.peek("s-probe", root)
        assert [e.get("denied") for e in after["stages"]] == [None], after["stages"]


def _transcript_with(runs: list) -> str:
    """A transcript holding Bash calls and what each printed."""
    import pathlib as pl
    import tempfile as tf
    rows = []
    for i, (command, printed) in enumerate(runs):
        rows.append(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "c%d" % i, "name": "Bash", "input": {"command": command}}]}}))
        rows.append(json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "c%d" % i, "content": printed}]}}))
    path = pl.Path(tf.mkdtemp()) / "session.jsonl"
    path.write_text("\n".join(rows) + "\n")
    return str(path)


def test_a_command_asked_and_answered_twice_is_not_asked_a_third_time() -> None:
    """Run 25's third claims round ran one probe five times, alternating with the same grep, and got
    the same two lines every time. Nothing refused it -- every call was legal -- and it spent a third
    of its round on a question it had already answered."""
    probe = "python3 -c 'print(1)'"
    with tempfile.TemporaryDirectory() as root:
        said = ask("Bash", {"command": probe}, root,
                   transcript=_transcript_with([(probe, "_VERBS=False"), (probe, "_VERBS=False")]))
        assert "printed the same thing" in said, said
        assert "_VERBS=False" in said, "the refusal withheld the output it says to use"


def test_a_command_whose_answer_keeps_changing_is_left_alone() -> None:
    """git status between edits, a suite between fixes: a command watching something move."""
    probe = "git status --short"
    with tempfile.TemporaryDirectory() as root:
        said = ask("Bash", {"command": probe}, root,
                   transcript=_transcript_with([(probe, "M one.py"), (probe, "M one.py M two.py")]))
        assert "printed the same thing" not in said, said


def test_one_repeat_is_a_retry_and_not_a_loop() -> None:
    probe = "pytest -q"
    with tempfile.TemporaryDirectory() as root:
        said = ask("Bash", {"command": probe}, root,
                   transcript=_transcript_with([(probe, "1 passed")]))
        assert "printed the same thing" not in said, said
