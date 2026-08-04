#!/usr/bin/env python3
"""Offline checks for the depth gate: synthetic sessions, no model, no network.

Each case builds a transcript in the shape Claude Code writes -- tool_use paired with tool_result,
a final assistant message carrying the claim blocks -- runs the hook as a subprocess exactly as the
client would, and asserts on what it decides. The point of driving it as a subprocess rather than
importing it is that fail-open is a property of the process, not of a function.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_GATE = str(_REPO / "scripts" / "cc-depth-gate.py")
sys.path.insert(0, str(_REPO / "scripts"))

import cc_evidence
import cc_ledger  # noqa: E402

SAMPLE = """def widen(rows):
    width = 0
    for row in rows:
        width = max(width, len(row))
    return width
"""


class Session:
    """A synthetic session: a repo with one file, some reads, and a final answer."""

    def __init__(self, tmp: str, reads=((1, 5),), answer: str = "", adapter: str = "review",
                 bash: int = 0, bash_command: str = "pytest -q", bash_output: str = "2 passed",
                 bash_failed: bool = False, commands=()):
        self.root = Path(tmp)
        self.session = "test-session"
        (self.root / "src").mkdir(parents=True, exist_ok=True)
        self.file = "src/widen.py"
        (self.root / self.file).write_text(SAMPLE)
        self.transcript = self.root / "transcript.jsonl"
        events = []
        n = 0
        for start, end in reads:
            n += 1
            tid = "t%d" % n
            events.append({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tid, "name": "Read",
                 "input": {"file_path": str(self.root / self.file)}}]}})
            events.append({"type": "user", "toolUseResult": {"type": "text", "file": {
                "filePath": str(self.root / self.file), "content": SAMPLE,
                "startLine": start, "numLines": end - start + 1, "totalLines": 5}},
                "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": tid, "is_error": False,
                     "content": SAMPLE}]}})
        # `bash` repeats one call; `commands` spells out a sequence, which is what an adapter
        # judged on how outcomes changed over the session needs.
        sequence = list(commands) or [(bash_command, bash_output, bash_failed)] * bash
        for i, (command, output, failed) in enumerate(sequence):
            tid = "b%d" % i
            events.append({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tid, "name": "Bash",
                 "input": {"command": command}}]}})
            events.append({"type": "user", "toolUseResult": output,
                           "message": {"role": "user", "content": [
                               {"type": "tool_result", "tool_use_id": tid,
                                "is_error": bool(failed), "content": output}]}})
        events.append({"type": "assistant", "message": {"role": "assistant",
                                                        "content": [{"type": "text",
                                                                     "text": answer}]}})
        self.transcript.write_text("\n".join(json.dumps(e) for e in events) + "\n")
        cc_ledger.write_contract(cc_ledger.contract_for(adapter), self.session, str(self.root))

    def run(self, stop_hook_active: bool = False, payload: dict | None = None,
            env: dict | None = None) -> dict:
        body = payload if payload is not None else {
            "session_id": self.session, "transcript_path": str(self.transcript),
            "cwd": str(self.root), "stop_hook_active": stop_hook_active,
        }
        environ = dict(os.environ)
        environ.update(env or {})
        proc = subprocess.run([sys.executable, _GATE], input=json.dumps(body),
                              capture_output=True, text=True, env=environ)
        assert proc.returncode == 0, "the gate must always exit 0: %s" % proc.stderr[-400:]
        # Failing open on an exception is deliberate, and it means a broken check allows everything
        # while every test still passes. One did: a helper read a dict as a string, the gate caught
        # it, and the only symptom was a test expecting a refusal getting silence.
        assert "Traceback" not in proc.stderr, \
            "the gate failed open on an exception:\n%s" % proc.stderr[-800:]
        return json.loads(proc.stdout) if proc.stdout.strip() else {}


def _blocks(answer: str, **kw) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        out = Session(tmp, answer=answer, **kw).run()
        return out.get("reason", "")


GOOD = ("CLAIM: widen returns the longest row length.\n"
        "EVIDENCE: src/widen.py:2-5\n"
        "QUOTE:\n"
        "    width = 0\n"
        "    for row in rows:\n"
        "        width = max(width, len(row))\n"
        "    return width\n")


def test_verified_answer_is_allowed() -> None:
    assert _blocks(GOOD) == "", "a checkable answer must pass"


def test_invented_quote_is_blocked() -> None:
    bad = GOOD.replace("        width = max(width, len(row))", "        width = row.length()")
    reason = _blocks(bad)
    assert reason, "a fabricated quote must be refused"
    assert "fail" in reason or "retouched" in reason, reason


def test_citation_no_read_covered_is_named() -> None:
    """The quote is real, but this session never read those lines."""
    with tempfile.TemporaryDirectory() as tmp:
        s = Session(tmp, reads=((1, 1),), answer=GOOD)
        reason = s.run().get("reason", "")
    assert "no read in this session covered" in reason, reason


def test_claim_without_evidence_is_blocked() -> None:
    reason = _blocks("CLAIM: this code is fine.\n")
    assert "cites nothing" in reason, reason


def test_unknown_is_a_legal_answer() -> None:
    answer = GOOD + "\nUNKNOWN: whether callers depend on the empty-input case.\n"
    assert _blocks(answer) == "", "declaring an unknown must not be penalised"


def test_adapter_probe_floor_is_enforced() -> None:
    """debug demands two commands actually run; describing them does not count."""
    reason = _blocks(GOOD, adapter="debug")
    assert "command(s) actually run" in reason, reason
    with tempfile.TemporaryDirectory() as tmp:
        ran = Session(tmp, answer=GOOD, adapter="debug", bash=2).run().get("reason", "")
    assert "command(s) actually run" not in ran, ran


HIGH = ("CLAIM: widen returns a wrong width on empty input.\n"
        "EVIDENCE: src/widen.py:2-5\n"
        "QUOTE:\n"
        "    width = 0\n"
        "    for row in rows:\n"
        "        width = max(width, len(row))\n"
        "    return width\n"
        "SEVERITY: high\n")


def test_a_falsification_nobody_ran_is_refused() -> None:
    """The gate found this in itself: the field was checked for existence, never for truth.

    Two high-severity claims passed carrying `Ran a test script calling evaluate ...` in a session
    whose own report said probes_run: 0.
    """
    reason = _blocks(HIGH + "FALSIFICATION: Ran a test script calling evaluate; no gap appeared.\n")
    assert "did not run" in reason, reason
    assert "no command was run at all" in reason, reason


def test_a_falsification_that_names_a_command_that_ran_is_accepted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        answer = HIGH + "FALSIFICATION: ran pytest -q against it and it printed 2 passed.\n"
        reason = Session(tmp, answer=answer, bash=1).run().get("reason", "")
    assert "did not run" not in reason, reason


def test_a_falsification_unrelated_to_anything_run_is_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        answer = HIGH + "FALSIFICATION: I ran hypothesis against it and it found nothing.\n"
        reason = Session(tmp, answer=answer, bash=1).run().get("reason", "")
    assert "did not run" in reason, reason
    assert "no command was run at all" not in reason, "a command did run, just not that one"


OPINION = ("CLAIM: The pipeline driver is structurally coupled to the proxy, making it difficult "
           "to add alternative backends without modifying the core invocation logic.\n"
           "EVIDENCE: src/widen.py:2-5\n"
           "QUOTE:\n"
           "    width = 0\n"
           "    for row in rows:\n"
           "        width = max(width, len(row))\n"
           "    return width\n")


def test_a_claim_that_names_nothing_wrong_is_refused() -> None:
    """Four review runs out of four produced this claim, and the adapter's stance did not stop it."""
    reason = _blocks(OPINION)
    assert "names nothing it does wrong" in reason, reason


def test_coupling_with_a_consequence_is_a_defect() -> None:
    """The narrowness that makes the rule tolerable: name the breakage and it is a claim again."""
    answer = OPINION.replace(
        "making it difficult to add alternative backends without modifying the core invocation "
        "logic.", "so a base URL change silently sends the run to the wrong model.")
    assert "names nothing it does wrong" not in _blocks(answer), _blocks(answer)


def test_an_opinion_is_legal_where_the_adapter_asks_for_one() -> None:
    """refactor-proposal exists to argue about design; the rule is review's alone."""
    reason = _blocks(OPINION, adapter="refactor-proposal")
    assert "names nothing it does wrong" not in reason, reason


ABSENT = ("CLAIM: nothing here handles the empty case.\n"
          "EVIDENCE: absence: zzz_no_such_token in *.py\n")


def test_an_absence_nobody_searched_for_is_refused() -> None:
    """A quote was checked against the transcript; an absence was checked against the disk alone,
    so a lucky guess passed with no search behind it. The gate found that about itself."""
    reason = _blocks(ABSENT, adapter="refactor-proposal")
    assert "nothing in this session looked for" in reason, reason


def test_an_absence_that_was_searched_for_is_accepted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reason = Session(tmp, answer=ABSENT, adapter="refactor-proposal", bash=1,
                         bash_command="rg -n zzz_no_such_token --glob '*.py'",
                         bash_output="", bash_failed=True).run().get("reason", "")
    assert "nothing in this session looked for" not in reason, reason


def test_naming_only_the_program_is_not_a_falsification() -> None:
    """The gate's own review found this, and proved it by running python3 and writing "I ran
    python3". Every session runs python3 or rg at some point."""
    with tempfile.TemporaryDirectory() as tmp:
        answer = HIGH + "FALSIFICATION: I ran pytest and nothing came of it.\n"
        reason = Session(tmp, answer=answer, bash=1,
                         bash_command="pytest -q tests/test_widen.py").run().get("reason", "")
    assert "did not run" in reason, reason


def test_a_pasted_command_is_a_falsification() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        answer = HIGH + "FALSIFICATION: pytest -q tests/test_widen.py printed 2 passed.\n"
        reason = Session(tmp, answer=answer, bash=1,
                         bash_command="pytest -q tests/test_widen.py").run().get("reason", "")
    assert "did not run" not in reason, reason


def test_sharing_only_a_flag_is_not_a_falsification() -> None:
    """The gate's review of its own fix: every second command has a -c."""
    with tempfile.TemporaryDirectory() as tmp:
        answer = HIGH + "FALSIFICATION: python3 repro_bypass.py printed Bypass result: True.\n"
        reason = Session(tmp, answer=answer, bash=1,
                         bash_command="python3 -c \"import cc_verify\"").run().get("reason", "")
    assert "did not run" in reason, reason


def test_a_one_word_command_needs_only_its_name() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        answer = HIGH + "FALSIFICATION: ran pytest, it printed 2 passed.\n"
        reason = Session(tmp, answer=answer, bash=1, bash_command="pytest").run().get("reason", "")
    assert "did not run" not in reason, reason


def test_a_probe_that_failed_still_counts_as_a_probe() -> None:
    """The refusal text has always said a failing probe is fine; the counter demanded success.

    It is the wrong way round: the command that demonstrates a defect is usually the one that exits
    non-zero.
    """
    with tempfile.TemporaryDirectory() as tmp:
        reason = Session(tmp, answer=GOOD, adapter="debug", bash=2,
                         bash_output="1 failed", bash_failed=True).run().get("reason", "")
    assert "command(s) actually run" not in reason, reason


def test_a_probe_the_client_refused_does_not_count() -> None:
    """A blocked call never reached a shell, so it is not a probe that failed."""
    with tempfile.TemporaryDirectory() as tmp:
        reason = Session(tmp, answer=GOOD, adapter="debug", bash=2,
                         bash_output="Context guard: refused, the window is at 82%",
                         bash_failed=True).run().get("reason", "")
    assert "command(s) actually run" in reason, reason


def test_second_pass_never_blocks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        s = Session(tmp, answer="CLAIM: unsupported\n")
        assert s.run().get("reason"), "first pass should block"
        assert s.run(stop_hook_active=True) == {}, "stop_hook_active must short-circuit"
        report = json.loads((Path(tmp) / "artifacts/depth" / s.session / "gate.json").read_text())
        assert report["final_pass"] is True and report["gaps"], report


def test_kill_switch_and_missing_contract_allow() -> None:
    """The switch is honoured only in a session launched to honour it. A stage that wanted past the
    read guard made the guard's switch itself and left it there, and the same file would have
    stopped this gate judging anything at all."""
    off = Path("/tmp/cc-depth-off")
    created = not off.exists()
    off.touch()
    try:
        assert _blocks("CLAIM: unsupported\n"), "a switch the stage could have made is not a switch"
        os.environ["CC_DEPTH_LIFTABLE"] = "1"
        assert _blocks("CLAIM: unsupported\n") == "", "the operator's switch must disable the gate"
    finally:
        os.environ.pop("CC_DEPTH_LIFTABLE", None)
        if created:
            off.unlink()
    with tempfile.TemporaryDirectory() as tmp:
        s = Session(tmp, answer="CLAIM: unsupported\n")
        (Path(tmp) / "artifacts/depth" / s.session / "contract.json").unlink()
        assert s.run() == {}, "an ungated session was never promised anything"


def test_fails_open_on_garbage() -> None:
    proc = subprocess.run([sys.executable, _GATE], input="not json at all",
                          capture_output=True, text=True)
    assert proc.returncode == 0 and proc.stdout.strip() == ""
    with tempfile.TemporaryDirectory() as tmp:
        s = Session(tmp, answer=GOOD)
        assert s.run(payload={"session_id": s.session, "transcript_path": "/nope.jsonl",
                              "cwd": str(tmp)}) == {}


def test_short_factual_answer_is_not_forced_into_a_ledger() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = Session(tmp, answer="It returns the longest row length.").run()
    assert out == {}, "a one-line factual answer should not demand a ledger"


def test_planted_fabrications_are_caught() -> None:
    """The plan's criterion: fabricated citations caught at 95 % or better."""
    planted = []
    body = SAMPLE.split("\n")
    planted.append(GOOD.replace("width = max(width, len(row))", "width = min(width, len(row))"))
    planted.append(GOOD.replace("src/widen.py:2-5", "src/widen.py:200-205"))
    planted.append(GOOD.replace("src/widen.py:2-5", "src/nonexistent.py:2-5"))
    planted.append(GOOD.replace("    return width", "    return width * 2"))
    planted.append(GOOD.replace("src/widen.py:2-5", "src/widen.py:1-2"))
    planted.append("CLAIM: rows are sorted first.\nEVIDENCE: src/widen.py:2-5\nQUOTE:\n"
                   "    rows = sorted(rows)\n")
    planted.append("CLAIM: there is a cache.\nEVIDENCE: src/widen.py:1-1\nQUOTE:\n"
                   "@lru_cache\n")
    planted.append(GOOD.replace("QUOTE:\n", "QUOTE:\n    # tidy up\n"))
    for i, line in enumerate(body[:2]):
        planted.append("CLAIM: fabricated %d.\nEVIDENCE: src/widen.py:3-4\nQUOTE:\n%s\n"
                       % (i, line + "  # invented"))
    caught = sum(1 for p in planted if _blocks(p))
    rate = caught / len(planted)
    assert rate >= 0.95, "caught %d/%d = %.0f%%" % (caught, len(planted), rate * 100)
    print("  planted fabrications caught: %d/%d" % (caught, len(planted)))


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  %s" % t.__name__)
    print("\n%d checks passed" % len(tests))
    return 0


if __name__ == "__main__":
    sys.exit(main())


def _load_gate():
    """Import the hook as a module despite the hyphen, so its internals can be unit-tested."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("depth_gate_under_test", _GATE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _assistant_line(text: str) -> str:
    return json.dumps({"type": "assistant", "message": {"role": "assistant",
                                                        "content": [{"type": "text", "text": text}]}})


def test_the_gate_waits_for_a_transcript_still_being_written() -> None:
    """The Stop hook can outrun the transcript write by tens of milliseconds.

    Measured in the false-premise arm: the answer's event was stamped 51 ms before the gate wrote
    its verdict about that same answer, and the verdict was "no claims were stated" about an answer
    that carried one. Here the file grows from a second thread while the gate reads it.
    """
    import threading
    import time
    gate = _load_gate()
    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "session.jsonl"
        transcript.write_text(_assistant_line("working on it") + "\n")

        def append_late() -> None:
            time.sleep(0.25)
            with open(transcript, "a") as fh:
                fh.write(_assistant_line("CLAIM: a thing\nEVIDENCE: f.py:1-1\nQUOTE:\nx = 1") + "\n")

        writer = threading.Thread(target=append_late)
        writer.start()
        text = gate._settled_text(str(transcript))
        writer.join()
        assert "CLAIM: a thing" in text, text


def test_settling_gives_up_rather_than_hanging() -> None:
    """A transcript that never stops growing must not wedge the stop; the budget is the ceiling."""
    import threading
    import time
    gate = _load_gate()
    with tempfile.TemporaryDirectory() as tmp:
        transcript = Path(tmp) / "busy.jsonl"
        transcript.write_text(_assistant_line("one") + "\n")
        stop = threading.Event()

        def keep_writing() -> None:
            while not stop.is_set():
                with open(transcript, "a") as fh:
                    fh.write(_assistant_line("more") + "\n")
                time.sleep(0.02)

        writer = threading.Thread(target=keep_writing)
        writer.start()
        started = time.time()
        gate._settled_text(str(transcript), budget=0.4)
        elapsed = time.time() - started
        stop.set()
        writer.join()
        assert elapsed < 1.5, "settling took %.2fs" % elapsed


def test_subagent_is_judged_on_its_own_transcript() -> None:
    """SubagentStop names two transcripts and the parent's is the wrong one.

    Measured against Claude Code 2.1.218: at SubagentStop the parent is still inside its Agent call,
    so `transcript_path` holds no answer. Reading it refused a delegate that had in fact produced a
    clean ledger, for "no claims were stated". The delegate's file is `agent_transcript_path`.
    """
    with tempfile.TemporaryDirectory() as tmp:
        sub = Session(tmp, answer=GOOD)
        parent = Path(tmp) / "parent.jsonl"
        parent.write_text(json.dumps({"type": "user", "message": {"role": "user",
                                                                  "content": "go"}}) + "\n")
        out = sub.run(payload={
            "session_id": sub.session, "transcript_path": str(parent),
            "agent_transcript_path": str(sub.transcript), "agent_id": "a1",
            "agent_type": "general-purpose", "cwd": str(sub.root),
            "hook_event_name": "SubagentStop", "stop_hook_active": False,
        })
        assert out == {}, "a delegate with a verified ledger must pass: %s" % out.get("reason", "")
        verdict = json.loads((Path(tmp) / "artifacts" / "depth" / sub.session / "a1" /
                              "gate.json").read_text())
        assert verdict["agent"] == "a1" and verdict["claims"] == 1


def test_the_payload_answer_beats_a_lagging_transcript() -> None:
    """`last_assistant_message` is authoritative: the client hands over what it is about to accept.

    Without it the gate races the transcript write and can judge the previous turn -- observed at
    51 ms of lag in the false-premise arm.
    """
    with tempfile.TemporaryDirectory() as tmp:
        session = Session(tmp, answer="still thinking out loud, no ledger here")
        out = session.run(payload={
            "session_id": session.session, "transcript_path": str(session.transcript),
            "cwd": str(session.root), "stop_hook_active": False,
            "last_assistant_message": GOOD,
        })
        assert out == {}, "the payload's answer should have been judged, not the stale one"


# --------------------------------------------------------------------------- #
# The implement adapter: judged on what changed, not on what is written down
# --------------------------------------------------------------------------- #
_SUITE = "python -m pytest tools/tests -q"
_ONE = "python -m pytest tools/tests/test_cash.py -q"
DONE = ("CLAIM: stale cash capacity is refreshed before the put sizing runs.\n"
        "EVIDENCE: command: %s -> 1 passed\n" % _ONE)


def _implement(commands, answer: str = DONE) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        session = Session(tmp, answer=answer, adapter="implement", commands=commands)
        return session.run().get("reason", "")


def test_implement_accepts_a_command_that_failed_and_then_passed() -> None:
    reason = _implement([
        (_ONE, "1 failed -- stale capacity used", True),
        (_ONE, "1 passed", False),
        (_SUITE, "1446 passed, 2 skipped", False),
    ])
    assert reason == "", reason


def test_all_green_from_the_start_is_not_evidence_of_a_fix() -> None:
    """Three passing runs cannot tell a fix from a no-op, so the adapter does not accept them."""
    reason = _implement([
        (_ONE, "1 passed", False),
        (_ONE, "1 passed", False),
        (_SUITE, "1446 passed, 2 skipped", False),
    ])
    assert "failed and then passed" in reason, reason


def test_a_narrowed_rerun_is_not_the_same_command() -> None:
    """The failure is shown on the suite and the pass on one test picked out of it.

    This is the cheapest way to fake a fix, and the reason the pair is matched on the literal
    command: whatever else was failing is still failing, out of frame.
    """
    reason = _implement([
        (_SUITE, "1 failed, 1445 passed", True),
        (_ONE + " -k cash_is_refreshed", "1 passed", False),
        (_ONE, "1 passed", False),
    ])
    assert "failed and then passed" in reason, reason


def test_implement_will_not_take_a_quote_of_the_diff_it_just_wrote() -> None:
    quoted = ("CLAIM: the capacity is now refreshed before sizing.\n"
              "EVIDENCE: src/widen.py:1-2\n"
              "QUOTE:\ndef widen(rows):\n    width = 0\n")
    reason = _implement([
        (_ONE, "1 failed", True),
        (_ONE, "1 passed", False),
        (_SUITE, "1446 passed", False),
    ], answer=quoted)
    assert "requires command_result evidence" in reason, reason


def test_a_red_that_is_only_a_missing_argument_is_not_a_red() -> None:
    """The first real implement run failed exactly here, and passed.

    Three tests asserted that a function now takes a ``holdings`` keyword. Removing the change made
    them raise TypeError, restoring it made them pass, and the pair looked like proof. It was not:
    no caller passed the new argument, so production behaviour was identical before and after.
    """
    reason = _implement([
        (_ONE, "TypeError: _put_cash_requirement() got an unexpected keyword argument 'holdings'",
         True),
        (_ONE, "1 passed", False),
        (_SUITE, "1449 passed, 2 skipped", False),
    ])
    assert "did not exist yet" in reason, reason


def test_a_red_on_a_wrong_value_is_accepted() -> None:
    """The same shape of run, failing on what the code computed rather than on its signature."""
    reason = _implement([
        (_ONE, "E       AssertionError: 1000000.0 != 0.0\n1 failed", True),
        (_ONE, "1 passed", False),
        (_SUITE, "1449 passed, 2 skipped", False),
    ])
    assert reason == "", reason


def test_an_unreadable_failure_is_given_the_benefit_of_the_doubt() -> None:
    """A check that cannot read the failure must not be the thing that refuses the answer."""
    reason = _implement([
        (_ONE, "", True),
        (_ONE, "1 passed", False),
        (_SUITE, "1449 passed", False),
    ])
    assert reason == "", reason


def _judge(commands, answer: str, predicted=(), adapter: str = "implement"):
    """evaluate() directly: a prediction reaches the gate as an argument, not through the payload."""
    gate = _load_gate()
    with tempfile.TemporaryDirectory() as tmp:
        session = Session(tmp, answer=answer, adapter=adapter, commands=commands)
        import cc_evidence
        calls = cc_evidence.collect(str(session.transcript))
        claims, unknowns = cc_ledger.claims_from_text(answer)
        gaps, _ = gate.evaluate(cc_ledger.contract_for(adapter), claims, unknowns, calls,
                                str(session.root), check_coverage=False, answer=answer,
                                predicted=tuple(predicted))
        return " ".join(gaps)


_PREDICTED = ({"kind": "command_result", "command": _ONE, "expect": "available_cash_czk 1000000"},)


def test_the_failure_must_be_the_one_the_plan_predicted() -> None:
    """A red that is real, behavioural, and about something else entirely."""
    gaps = _judge([
        (_ONE, "E       AssertionError: quantity 3 != 2\n1 failed", True),
        (_ONE, "1 passed", False),
        (_SUITE, "1449 passed", False),
    ], DONE, predicted=_PREDICTED)
    assert "did not print what the plan said" in gaps, gaps


def test_the_predicted_failure_is_accepted() -> None:
    gaps = _judge([
        (_ONE, "E       AssertionError: available_cash_czk 1000000 != 0\n1 failed", True),
        (_ONE, "1 passed", False),
        (_SUITE, "1449 passed", False),
    ], DONE, predicted=_PREDICTED)
    assert gaps == "", gaps


def test_without_a_prediction_the_older_rule_still_applies() -> None:
    """Nothing predicted -- an interactive session, or a plan stage that was skipped."""
    gaps = _judge([
        (_ONE, "TypeError: f() got an unexpected keyword argument 'holdings'", True),
        (_ONE, "1 passed", False),
        (_SUITE, "1449 passed", False),
    ], DONE)
    assert "did not exist yet" in gaps, gaps


def test_a_plan_must_name_the_failure_it_expects() -> None:
    plan = ("CLAIM: the capacity is read from disk here.\n"
            "EVIDENCE: src/widen.py:1-2\n"
            "QUOTE:\ndef widen(rows):\n    width = 0\n")
    gaps = _judge([], plan, adapter="change-plan")
    assert "commits to nothing" in gaps, gaps
    assert "predicted" in gaps or "PREDICT" in gaps, gaps


def test_the_implement_rules_do_not_reach_other_adapters() -> None:
    """Scope, asserted rather than assumed.

    Replaying eight recorded review runs showed these rules contributing nothing, which is the
    intended answer and also exactly what a silently mis-scoped rule looks like until the day a
    review of a repository with an uncommitted diff starts being refused for its test style.
    """
    for adapter in ("review", "debug", "refactor-proposal", "ops-perf", "bench-audit"):
        contract = cc_ledger.contract_for(adapter)
        assert not contract.needs_red_green, adapter
        assert not contract.needs_prediction, adapter
    gaps = _judge([("pytest -q", "1 passed", False)] * 3, GOOD, adapter="review")
    for phrase in ("failed and then passed", "asserts only that a mock was called",
                   "is never passed by any caller", "commits to nothing"):
        assert phrase not in gaps, gaps


def test_the_contract_shows_a_filled_in_block_not_only_a_schema() -> None:
    """A schema of angle brackets was all it said, and stages filled it in eight different ways --
    "line 212 of guard.py", the quote in the EVIDENCE sentence, the ledger in a file with prose in
    the answer. Each was correct work refused on form."""
    text = cc_ledger.contract_markdown(cc_ledger.contract_for("review"))
    assert "EVIDENCE: scripts/cc-context-guard.py:213-213" in text, text
    assert "carries a colon before its line numbers" in text, text


def test_a_turn_that_stopped_before_answering_is_told_that() -> None:
    """Every first round of a claims stage has ended on a sentence like "Now let me run the actual
    tests and verify each claim". Telling it no claims were stated reads as a quarrel about blocks
    when what it needs to hear is that it stopped in the middle."""
    gaps, _ = _load_gate().evaluate(cc_ledger.contract_for("review"), [], [], [], ".",
                            check_coverage=False,
                            answer="Now let me run the actual tests and verify each claim.")
    assert any("ended before you answered" in g for g in gaps), gaps


def test_a_ledger_with_no_claims_is_still_told_about_blocks() -> None:
    gaps, _ = _load_gate().evaluate(cc_ledger.contract_for("review"), [], [], [], ".",
                            check_coverage=False, answer="CLAIM\nbut nothing parseable")
    assert any("No claims were stated" in g for g in gaps), gaps


def test_a_long_report_in_the_wrong_shape_is_not_told_it_stopped_early() -> None:
    """A seven-finding report was told its turn had ended before it answered. It had answered; it
    used the word Observation, and the complaint has to be about the shape, not about stopping."""
    report = "\n\n".join("**Observation %d: something is true here.** I looked and saw it." % i
                         for i in range(1, 9))
    gaps, _ = _load_gate().evaluate(cc_ledger.contract_for("review"), [], [], [], ".",
                                    check_coverage=False, answer=report)
    assert any("No claims were stated" in g for g in gaps), gaps
    assert not any("ended before you answered" in g for g in gaps), gaps


def test_a_file_edited_under_a_stage_is_not_the_stage_lying() -> None:
    """Twice in one afternoon a stage quoted a file verbatim, the file was edited while it worked,
    and the gate reported "quote not present in" -- which reads as fabrication and is not."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "g.py"
        target.write_text("def guard():\n    return 'new text'\n")
        shown = "  1  def guard():\n  2      return 'old text'\n"
        call = cc_evidence.ToolCall(agent="claims", tool="Read", call_id="1",
                                    args={"file_path": str(target)}, text=shown)
        answer = "CLAIM: the guard returns the old text\nQUOTE: g.py:2 `    return 'old text'`\n"
        claims, unknowns = cc_ledger.claims_from_text(answer, tmp)
        gaps, report = _load_gate().evaluate(cc_ledger.contract_for("review"), claims, unknowns,
                                             [call], tmp, check_coverage=False, answer=answer)
        assert not gaps, gaps
        assert report.get("moved"), report


def test_a_quote_nothing_read_is_still_a_gap() -> None:
    """The excuse rests on the transcript showing the text. Bash output does not count: a stage can
    print whatever it likes with echo, and a Read result is written by the client."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "g.py").write_text("def guard():\n    return 'new text'\n")
        faked = cc_evidence.ToolCall(agent="claims", tool="Bash", call_id="1",
                                     args={"command": "echo"}, text="    return 'old text'")
        answer = "CLAIM: the guard returns the old text\nQUOTE: g.py:2 `    return 'old text'`\n"
        claims, unknowns = cc_ledger.claims_from_text(answer, tmp)
        gaps, _ = _load_gate().evaluate(cc_ledger.contract_for("review"), claims, unknowns,
                                        [faked], tmp, check_coverage=False, answer=answer)
        assert gaps, "a quote only Bash showed is not evidence the file moved"


def test_the_contract_states_the_cap_it_will_be_enforced_against() -> None:
    """The cap was enforced and never stated. A stage that found one class of bypass wrote 188
    numbered variants of it, and refusing that against an unstated limit would have been the gate's
    fault rather than the stage's."""
    contract = cc_ledger.contract_for("review")
    text = cc_ledger.contract_markdown(contract)
    assert "At most %d claims" % contract.claim_cap in text, text
    assert "one claim per instance" in text, text


def test_a_claim_that_holds_is_written_down_though_its_neighbours_fail() -> None:
    """The gate used to record only what went wrong, so a round refused for one bad citation threw
    away every good one with it. Run 21 lost six verified findings that way. What passes is now
    recorded per claim, with the citation, so a stage that is given up on can still be quoted."""
    gate = _load_gate()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "widen.py").write_text("def widen(rows):\n    width = 0\n    return width\n")
        ledger = ("CLAIM: widen starts the width at zero.\n"
                  "EVIDENCE: widen.py:2-2\n"
                  "QUOTE:\n"
                  "    width = 0\n"
                  "CLAIM: widen raises on an empty list.\n"
                  "EVIDENCE: widen.py:3-3\n"
                  "QUOTE:\n"
                  "    raise ValueError('empty')\n")
        claims, unknowns = cc_ledger.claims_from_text(ledger, str(root))
        gaps, report = gate.evaluate(cc_ledger.contract_for("review"), claims, unknowns, [],
                                     str(root), check_coverage=False, answer=ledger)
        assert gaps, "the fabricated quote must still be refused"
        assert [f["claim"] for f in report["stood"]] == ["widen starts the width at zero."], report
        assert report["stood"][0]["cites"] == ["widen.py:2"], report["stood"]


def test_a_finding_whose_file_moved_is_not_reported_as_proved() -> None:
    """A quote the file no longer bears is excused when the transcript shows the stage was shown it,
    because it quoted what it saw. Excused is not verified: nobody has checked it against the tree as
    it stands, so it must not be carried into an answer as something the run established."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "g.py"
        target.write_text("def guard():\n    return 'new text'\n")
        call = cc_evidence.ToolCall(agent="claims", tool="Read", call_id="1",
                                    args={"file_path": str(target)},
                                    text="  1  def guard():\n  2      return 'old text'\n")
        answer = "CLAIM: the guard returns the old text\nQUOTE: g.py:2 `    return 'old text'`\n"
        claims, unknowns = cc_ledger.claims_from_text(answer, tmp)
        gaps, report = _load_gate().evaluate(cc_ledger.contract_for("review"), claims, unknowns,
                                             [call], tmp, check_coverage=False, answer=answer)
        assert not gaps, gaps
        assert report["stood"] == [], "an excused citation is not a proved one: %s" % report["stood"]


def _probe(command: str, printed: str, ok: bool = True):
    return cc_evidence.ToolCall(agent="claims", tool="Bash", call_id="p1",
                               args={"command": command}, ok=ok, text=printed)


def test_a_review_proved_by_running_the_thing_is_not_refused_for_lacking_quotes() -> None:
    """A rule whose job is to refuse things is best established by being refused by it. Run 21's
    claims stage did exactly that, cited 25 commands and no file quotes, and was refused in all four
    rounds for the kind of its evidence rather than the truth of it -- which is why the stage was
    abandoned and the adversary never ran."""
    calls = [_probe("python3 guard.py --check rm", "ALLOWED: rm is not blocked")]
    answer = ("CLAIM: rm is not blocked by the tamper rule.\n"
              "EVIDENCE: command: python3 guard.py --check rm -> ALLOWED: rm is not blocked\n")
    claims, unknowns = cc_ledger.claims_from_text(answer, ".")
    gaps, report = _load_gate().evaluate(cc_ledger.contract_for("review"), claims, unknowns, calls,
                                        ".", check_coverage=False, answer=answer)
    assert not any("requires" in g for g in gaps), gaps
    assert [f["claim"] for f in report["stood"]] == ["rm is not blocked by the tamper rule."], report


def test_a_review_that_checked_nothing_is_still_refused() -> None:
    """The requirement became an alternative, not an absence: a ledger resting on neither a quote
    nor a command it ran has nothing the gate can check, whatever kind it claims to be."""
    answer = "CLAIM: the guard is sound.\nEVIDENCE: command: python3 guard.py -> it was fine\n"
    claims, unknowns = cc_ledger.claims_from_text(answer, ".")
    gaps, _ = _load_gate().evaluate(cc_ledger.contract_for("review"), claims, unknowns, [], ".",
                                    check_coverage=False, answer=answer)
    assert any("no recorded command" in g for g in gaps), gaps


def test_an_adapter_that_wants_two_kinds_still_wants_both() -> None:
    """Alternatives are per requirement, not a general loosening. A refactor proposal must show the
    lines it would change and a search proving what is not there; one of the two is not the pair."""
    answer = ("CLAIM: the helper is unused.\n"
              "EVIDENCE: widen.py:1-1\n"
              "QUOTE:\n"
              "def widen(rows):\n")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "widen.py").write_text("def widen(rows):\n    return 0\n")
        claims, unknowns = cc_ledger.claims_from_text(answer, tmp)
        gaps, _ = _load_gate().evaluate(cc_ledger.contract_for("refactor-proposal"), claims,
                                        unknowns, [], tmp, check_coverage=False, answer=answer)
        assert any(cc_ledger.ABSENCE in g for g in gaps), gaps


def test_a_code_quote_under_a_command_is_not_reported_as_its_output() -> None:
    """A QUOTE under a command citation is sometimes the output and sometimes the code the claim
    rests on. Reading it as the output told two of run 21's claims that their command had printed a
    regex out of the file under review, which is a refusal nobody could act on."""
    calls = [_probe("python3 guard.py --check unlink", "")]
    answer = ("CLAIM: unlink is missing from the verbs.\n"
              "EVIDENCE: command: python3 guard.py --check unlink\n"
              "QUOTE:\n"
              "_VERBS = re.compile(r\"touch|mv|cp\")\n")
    claims, unknowns = cc_ledger.claims_from_text(answer, ".")
    gaps, _ = _load_gate().evaluate(cc_ledger.contract_for("review"), claims, unknowns, calls, ".",
                                    check_coverage=False, answer=answer)
    assert any("not what it printed" in g for g in gaps), gaps
    assert not any("printed anything like" in g for g in gaps), gaps


def test_output_written_under_a_quote_header_is_still_the_output() -> None:
    """The arrow is punctuation. A stage that put what the command printed under its own header has
    said what it printed, and is held to it rather than refused for the shape."""
    calls = [_probe("pytest -q", "294 passed in 78s")]
    answer = ("CLAIM: the suite passes.\n"
              "EVIDENCE: command: pytest -q\n"
              "QUOTE:\n"
              "294 passed in 78s\n")
    claims, unknowns = cc_ledger.claims_from_text(answer, ".")
    gaps, report = _load_gate().evaluate(cc_ledger.contract_for("review"), claims, unknowns, calls,
                                        ".", check_coverage=False, answer=answer)
    assert not gaps, gaps
    assert report["stood"], report


def test_a_contract_read_back_from_disk_keeps_its_alternatives() -> None:
    """The contract crosses a process boundary as JSON, and a group that arrives as a list is a
    requirement for a kind named `['file_quote', 'command_result']`, which nothing can satisfy."""
    with tempfile.TemporaryDirectory() as tmp:
        cc_ledger.write_contract(cc_ledger.contract_for("review"), "s1", tmp)
        back = cc_ledger.load_contract("s1", tmp)
        assert cc_ledger.wants(back) == [(cc_ledger.FILE_QUOTE, cc_ledger.COMMAND_RESULT)], back


def test_an_answer_of_unknowns_and_no_claims_is_not_refused_for_stating_none() -> None:
    """The refusal said "or state UNKNOWN for what you could not establish" to an answer that had
    stated exactly that. It is also what an adversary produces when it works: the stance tells it to
    delete every claim its attack kills, so killing all of them leaves unknowns and no claims.
    Refusing it teaches that a finding is safer invented than withheld."""
    gate = _load_gate()
    contract = cc_ledger.contract_for("review")
    answer = ("UNKNOWN: every claim fell -- each was about a regex guarding a switch that also "
              "needs an environment variable no stage can set, so none of them is a way past it.\n")
    import cc_verify
    claims, unknowns = cc_verify.parse_ledger(answer, root=".")
    gaps, _ = gate.evaluate(contract, claims, unknowns, [], ".", answer=answer)
    assert not any("No claims were stated" in g for g in gaps), gaps


def test_an_answer_of_neither_claims_nor_unknowns_is_still_refused() -> None:
    """Prose remains prose."""
    gate = _load_gate()
    contract = cc_ledger.contract_for("review")
    answer = "I looked at the rule and it seems broadly fine to me, on balance. " * 12
    gaps, _ = gate.evaluate(contract, [], [], [], ".", answer=answer)
    assert any("No claims were stated" in g for g in gaps), gaps


def _closing_tree() -> str:
    """A tiny tracked tree, since the check asks git what the tree holds."""
    import pathlib as pl
    import subprocess, tempfile
    root = tempfile.mkdtemp()
    pl.Path(root, "src.py").write_text("def keeper():\n    return OFF_SWITCH.exists()\n")
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git"] + args, cwd=root, capture_output=True)
    return root


def test_a_quotation_in_the_closing_message_that_is_nowhere_is_caught() -> None:
    """Run 22 closed on four verified findings and three fenced blocks nobody wrote."""
    gate = _load_gate()
    root = _closing_tree()
    text = "Here is the offending code:\n\n```python\n    return TAMPER_SWITCH.absent()\n```\n"
    assert gate._fabricated_quotes(text, root), "a made-up quotation went unnoticed"


def test_a_quotation_that_is_in_the_tree_is_left_alone() -> None:
    gate = _load_gate()
    root = _closing_tree()
    text = "The check reads:\n\n```python\n    return OFF_SWITCH.exists()\n```\n"
    assert gate._fabricated_quotes(text, root) == [], "an honest quotation was called invention"


def test_a_quotation_that_was_rewrapped_or_elided_is_still_left_alone() -> None:
    """A stage that elides or reindents has still quoted the file, and the closing message has to be
    allowed to finish."""
    gate = _load_gate()
    root = _closing_tree()
    text = ("```python\ndef keeper():\n    # ... elided ...\n    something new entirely here\n```\n")
    assert gate._fabricated_quotes(text, root) == [], "elision was read as invention"


def test_a_command_shown_in_the_closing_message_is_not_a_quotation() -> None:
    """A command a stage typed is nowhere on disk by design."""
    gate = _load_gate()
    root = _closing_tree()
    text = "I ran:\n\n```bash\ntouch /tmp/cc-guard-off && echo denied\n```\n"
    assert gate._fabricated_quotes(text, root) == [], "a run was judged as a quotation"



def _read_of(path: Path, lines: list[str], first: int, count: int):
    """A Read the client would have written, showing `count` lines from `first`."""
    shown = lines[first - 1:first - 1 + count]
    return cc_evidence.ToolCall(
        agent="claims", tool="Read", call_id="r1",
        args={"file_path": str(path), "offset": first, "limit": count},
        text="\n".join("%6d|%s" % (first + i, line) for i, line in enumerate(shown)),
        detail={"file": {"filePath": str(path), "startLine": first, "numLines": count}})


def _judge_review(answer: str, calls: list, root: str):
    claims, unknowns = cc_ledger.claims_from_text(answer, root)
    return _load_gate().evaluate(cc_ledger.contract_for("review"), claims, unknowns, calls, root,
                                 answer=answer)


# The four ways a citation can be invented, run against the gate as one set. Each was a live failure
# at some point, and each is cheap to reintroduce by loosening a check that looked over-strict.
def test_the_four_ways_of_inventing_a_citation_are_all_refused() -> None:
    with tempfile.TemporaryDirectory() as root:
        body = ["# one", "def f(x):", "    limit = SHORT if x else LONG", "    return limit"]
        source = Path(root, "s.py")
        source.write_text("\n".join(body) + "\n")
        whole = [_read_of(source, body, 1, 4)]

        wrong_lines = ("CLAIM 1: the limit depends on x.\n"
                       "EVIDENCE: file_quote s.py lines 40-40\nQUOTE:\n%s\n" % body[2])
        gaps, _ = _judge_review(wrong_lines, whole, root)
        assert gaps and "outside" in gaps[0], gaps

        absent = ("CLAIM 1: the limit is always LONG.\n"
                  "EVIDENCE: file_quote s.py lines 3-3\nQUOTE:\n    limit = LONG\n")
        gaps, _ = _judge_review(absent, whole, root)
        assert gaps and "not present" in gaps[0], gaps

        unread = ("CLAIM 1: the limit depends on x.\n"
                  "EVIDENCE: file_quote s.py lines 3-3\nQUOTE:\n%s\n" % body[2])
        gaps, _ = _judge_review(unread, [_read_of(source, body, 1, 1)], root)
        assert gaps and "no read in this session covered" in gaps[0], gaps

        unrun = ("CLAIM 1: the cap is three.\n"
                 "EVIDENCE: command: rg -n SHORT s.py -> SHORT = 3\n")
        gaps, _ = _judge_review(unrun, whole, root)
        assert gaps and "no recorded command" in gaps[0], gaps


def test_a_true_quote_carrying_a_false_claim_is_the_adversary_s_job_and_is_named_as_such() -> None:
    """The one channel nothing mechanical closes, asserted so that nobody assumes it is closed.

    A quote can be verbatim, in the named file, at the given lines, and read in this session, and
    still not say what the claim says. `limit = SHORT if x else LONG` passes every check while
    carrying "the limit is SHORT whenever x is set" -- which it happens to support -- or "the limit
    is SHORT when x is unset", which it contradicts. The gate cannot tell those apart, so the
    adversary stage is told to, and this pins both halves: the gate lets it through, and the
    instruction to catch it exists.
    """
    import cc_flow
    with tempfile.TemporaryDirectory() as root:
        body = ["def f(x):", "    limit = SHORT if x else LONG"]
        source = Path(root, "s.py")
        source.write_text("\n".join(body) + "\n")
        inverted = ("CLAIM 1: the limit is SHORT when x is unset.\n"
                    "EVIDENCE: file_quote s.py lines 2-2\nQUOTE:\n%s\n" % body[1])
        gaps, report = _judge_review(inverted, [_read_of(source, body, 1, 2)], root)
        assert not gaps, ("the gate has learned to read code, which it has not -- if this now "
                          "passes, the claim below about needing the adversary is out of date: %s"
                          % gaps)
        assert report["stood"], "a verified citation should still be recorded as having stood"

        adversary = cc_flow.stage_in("review", "adversary")
        assert "fit between each claim and its quote" in adversary.stance, (
            "nothing mechanical catches this, so the adversary must be told to")
