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
        args: list[str] | None = None) -> str:
    """The guard's decision on one call: the refusal it would send, or "" for allowed."""
    payload = {"hook_event_name": "PreToolUse", "session_id": session, "cwd": root,
               "tool_name": tool, "tool_input": tool_input, "transcript_path": ""}
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
