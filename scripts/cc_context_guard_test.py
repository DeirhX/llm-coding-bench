#!/usr/bin/env python3
"""Every branch of scripts/cc-context-guard.py, including the ones that must allow.

A hook that refuses too much is worse than no hook, and worse still if the refusal is unescapable:
the first version of this guard counted its own refusals as reads, so the narrower retry it demanded
was refused as a duplicate, leaving the model looping. That case is `refused read does not poison`
below, and it is the reason this file exists rather than a handful of throwaway checks.

Synthetic transcripts and fixtures only -- no client, no model, no GPU. Run it after any change:

    python3 scripts/cc_context_guard_test.py
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

GUARD = Path(__file__).resolve().parent / "cc-context-guard.py"
TMP = Path("/tmp/guard_test")
OFF = Path("/tmp/cc-guard-off")


def setup():
    TMP.mkdir(exist_ok=True)
    OFF.unlink(missing_ok=True)
    big = TMP / "big.py"
    big.write_text("\n".join(f"line {i} = {i}" for i in range(1200)))
    small = TMP / "small.py"
    small.write_text("\n".join(f"line {i}" for i in range(80)))
    # the fixture must predate any recorded read, or "unchanged since" cannot be true
    old = time.time() - 3600
    os.utime(big, (old, old))
    os.utime(small, (old, old))
    return big, small


def transcript(name: str, records) -> Path:
    path = TMP / name
    with path.open("w") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    return path


def read_call(path: str, when: str, offset=None, limit=None, use_id="toolu_a"):
    args = {"file_path": path}
    if offset:
        args["offset"] = offset
    if limit:
        args["limit"] = limit
    return {"timestamp": when, "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": use_id, "name": "Read", "input": args}]}}


def error_result(use_id: str, when: str):
    return {"timestamp": when, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": use_id, "is_error": True,
         "content": "Context guard: refused"}]}}


def bulk(tokens: int):
    return [{"timestamp": "2026-07-30T10:00:00.000Z", "message": {"role": "user", "content": [
        {"type": "tool_result", "content": "x" * (tokens * 4)}]}}]


def run(tool, tool_input, tpath, extra=()):
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
               "transcript_path": str(tpath), "cwd": str(TMP)}
    proc = subprocess.run([sys.executable, str(GUARD), *extra], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return "CRASH", proc.stderr.strip()[:200]
    if not proc.stdout.strip():
        return "allow", ""
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    return out.get("permissionDecision"), out.get("permissionDecisionReason", "")


def main():
    big, small = setup()
    now = time.time()
    # fixtures are stamped an hour old, so a read *before* that means the file changed afterwards,
    # and a read after it means the file has not changed since. Getting these the wrong way round
    # is easy: the first version of this test called a file changed when it was merely older.
    ancient = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now - 7200))
    future = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now + 3600))

    empty = transcript("empty.jsonl", [])
    unchanged = transcript("unchanged.jsonl", [read_call(str(big), future)])
    changed = transcript("changed.jsonl", [read_call(str(big), ancient)])
    region = transcript("region.jsonl", [read_call(str(big), future, offset=1, limit=100)])
    poisoned = transcript("poisoned.jsonl", [read_call(str(big), future, use_id="toolu_p"),
                                             error_result("toolu_p", future)])
    full = transcript("full.jsonl", bulk(80000))

    cases = [
        # the read guard
        ("unbounded read of a 1,200-line file", "Read", {"file_path": str(big)}, empty, (), "deny"),
        ("bounded read of the same file", "Read",
         {"file_path": str(big), "offset": 40, "limit": 60}, empty, (), "allow"),
        ("unbounded read of an 80-line file", "Read", {"file_path": str(small)}, empty, (), "allow"),
        ("a limit larger than the cap is unbounded", "Read",
         {"file_path": str(big), "offset": 1, "limit": 5000}, empty, (), "deny"),
        ("a limit at the cap", "Read",
         {"file_path": str(big), "offset": 1, "limit": 500}, empty, (), "allow"),
        # the duplicate guard
        ("re-read of a file unchanged since", "Read",
         {"file_path": str(big), "offset": 1, "limit": 50}, unchanged, (), "deny"),
        ("re-read of a file changed since", "Read",
         {"file_path": str(big), "offset": 1, "limit": 50}, changed, (), "allow"),
        ("a region the earlier read did not cover", "Read",
         {"file_path": str(big), "offset": 900, "limit": 50}, region, (), "allow"),
        ("refused read does not poison the file", "Read",
         {"file_path": str(big), "offset": 1, "limit": 80}, poisoned, (), "allow"),
        # the bash loopholes
        ("cat of a large file", "Bash", {"command": f"cat {big}"}, empty, (), "deny"),
        ("head -n 3000 of a large file", "Bash",
         {"command": f"head -n 3000 {big}"}, empty, (), "deny"),
        ("head -n 20 of a large file", "Bash", {"command": f"head -n 20 {big}"}, empty, (), "allow"),
        ("a search that pipes", "Bash", {"command": f"rg foo {big} | head -20"}, empty, (), "allow"),
        # the stop threshold
        ("read at 82% of the window", "Read", {"file_path": str(small)}, full, (), "deny"),
        ("Write at 82%", "Write", {"file_path": str(TMP / "NOTES.md"), "contents": "x"},
         full, (), "allow"),
        ("Edit at 82%", "Edit", {"file_path": str(small), "old_string": "a", "new_string": "b"},
         full, (), "allow"),
        ("git commit at 82%", "Bash", {"command": "git commit -m wip"}, full, (), "allow"),
        ("git status --short at 82%", "Bash", {"command": "git status --short"},
         full, (), "allow"),
        ("pytest at 82%", "Bash", {"command": "python -m pytest -q"}, full, (), "deny"),
        ("bulk call under a raised threshold", "Read", {"file_path": str(small)}, full,
         ("--stop-pct", "95"), "allow"),
        ("a read-only stage is told to answer, not to write notes", "Read", {"file_path": str(small)},
         full, ("--stop-advice", "answer"), "deny"),
        # failing open
        ("missing transcript", "Read", {"file_path": str(small)},
         TMP / "nonexistent.jsonl", (), "allow"),
        ("nonexistent file", "Read", {"file_path": str(TMP / "gone.py")}, empty, (), "allow"),
    ]

    failures = 0
    for label, tool, tool_input, tpath, extra, expected in cases:
        got, reason = run(tool, tool_input, tpath, extra)
        ok = got == expected
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  {label:<42} expected {expected:<5} got {got}")
        if got == "CRASH":
            print(f"          {reason}")
        elif not ok and reason:
            print(f"          {' '.join(reason.split())[:104]}")

    # The advice a refusal carries has to name a tool the stage actually has, so check the wording
    # and not merely the verdict: a pipeline stage has no Write.
    _, said = run("Read", {"file_path": str(small)}, full, ("--stop-advice", "answer"))
    ok = "Answer now" in said and "NOTES.md" not in said
    failures_in_wording = 0 if ok else 1
    print(f"  {'ok  ' if ok else 'FAIL'}  {'and its wording names no missing tool':<42} "
          f"expected answer-advice got {'it' if ok else said[:80]}")

    # A stage backgrounded a test run and polled it with `sleep 180 && tail`, spending twenty
    # minutes of a fifty-minute budget waiting for a suite that takes under three seconds.
    for command, expected, label in (
            ("sleep 180 && tail -20 /tmp/task.log", "deny", "a long sleep is refused"),
            ("sleep 2 && pytest -q", "allow", "a short sleep is fine"),
            ("pytest -q --timeout 300", "allow", "a big number that is not a sleep"),
            # A stage reviewing this rule was denied three times for writing `sleep 180` as data.
            ("""python3 -c "print('sleep 180')" """, "allow", "a sleep inside a quoted string"),
            ("cat > /tmp/x.py << 'EOF'\ncheck('sleep 180')\nEOF", "allow", "a sleep in a heredoc"),
            ("(cd /tmp && sleep 90)", "deny", "a sleep in a subshell still counts"),
            ("pytest -q; sleep 120", "deny", "a sleep after a semicolon still counts"),
    ):
        got, said = run("Bash", {"command": command}, empty)
        ok = got == expected
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  {label:<42} expected {expected:<5} got {got}")

    # A claims stage wrote its whole ledger to claims.jsonl and summarised it in prose, so the gate
    # judged four cited findings as citing nothing.
    ledger = "CLAIM: the rule is broader than its intent\nEVIDENCE: file_quote\nQUOTE: x\n"
    got, said = run("Write", {"file_path": "/tmp/guard_test/claims.jsonl", "content": ledger}, empty)
    ok = got == "deny" and "message you finish with" in said
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'}  {'a ledger written to a file is refused':<42} "
          f"expected deny  got {got}")

    # ...but the repo's own sources quote those headers, and an implement stage must be able to edit
    # them.
    source = '"""The contract."""\n\nCLAIMS = "CLAIM: ...\\nEVIDENCE: ...")\n'
    got, said = run("Write", {"file_path": "/tmp/guard_test/cc_ledger.py", "content": source}, empty)
    ok = got == "allow"
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'}  {'source that quotes the headers is fine':<42} "
          f"expected allow got {got}")

    got, said = run("Write", {"file_path": str(small)}, empty, ("--deny", "Write,Edit"))
    ok = got == "deny" and "judging" in said
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'}  {'a judging stage may not write':<42} "
          f"expected deny  got {got}")

    # A parent read a 231,800-byte subagent transcript in 67 lines: roughly 58,000 tokens, waved
    # through by a rule that counts lines.
    fat = TMP / "transcript.jsonl"
    fat.write_text("\n".join('{"role":"assistant","text":"%s"}' % ("x" * 4000) for _ in range(40)))
    got, said = run("Read", {"file_path": str(fat)}, empty)
    ok = got == "deny" and "bytes" in said
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'}  {'a few enormous lines are refused':<42} "
          f"expected deny  got {got}")

    got, _ = run("Read", {"file_path": str(fat), "offset": 1, "limit": 20}, empty)
    ok = got == "allow"
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'}  {'a slice of it is allowed':<42} "
          f"expected allow got {got}")

    OFF.touch()
    got, _ = run("Read", {"file_path": str(big)}, empty)
    ok = got == "deny"
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'}  {'off-switch without the flag':<42} expected deny  got {got}")

    os.environ["CC_GUARD_LIFTABLE"] = "1"
    got, _ = run("Read", {"file_path": str(big)}, empty)
    ok = got == "allow"
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'}  {'off-switch in a liftable session':<42} expected allow got {got}")
    del os.environ["CC_GUARD_LIFTABLE"]
    OFF.unlink(missing_ok=True)

    failures += failures_in_wording
    print(f"\n{'SELFTEST OK' if not failures else f'{failures} FAILURES'} "
          f"({len(cases) + 6} cases)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())


def test_a_switch_the_model_made_itself_does_not_lift_the_guard():
    """The rule that matters, because the pattern rule below it can be walked around by a shell and
    this cannot: an off-switch is honoured only in a session launched to honour it."""
    big, _ = setup()
    OFF.touch()
    try:
        assert run("Read", {"file_path": str(big)}, TMP / "none.jsonl")[0] == "deny"
    finally:
        OFF.unlink(missing_ok=True)


def test_the_model_may_not_turn_the_guard_off():
    """A survey stage ran `touch /tmp/cc-guard-off`, described it as "Temporarily disable guard for
    reading", and left it there. Every stage after it, and every session started afterwards, ran
    unguarded. The switch belongs to whoever is at the keyboard."""
    big, _ = setup()
    for command in ("touch /tmp/cc-guard-off",
                    "touch /tmp/cc-depth-off && echo done",
                    "python3 -c \"open('/tmp/cc-guard-off','w')\"",
                    "echo x > /tmp/cc-guard-off",
                    "echo x >> /tmp/cc-guard-off",
                    "cd /tmp && touch cc-guard-off",
                    # Found by a review of this rule, which ran each of them against the hook:
                    # neither dd nor ln was a write verb, and both make the file.
                    "dd of=/tmp/cc-guard-off",
                    "ln -s /dev/null /tmp/cc-guard-off"):
        decision, _ = run("Bash", {"command": command}, TMP / "none.jsonl")
        assert decision == "deny", command
    decision, _ = run("Write", {"file_path": "/tmp/cc-guard-off", "content": ""},
                      TMP / "none.jsonl")
    assert decision == "deny"


def test_putting_the_guard_back_is_not_tampering():
    """Removing a switch leaves the guard stricter than it found it, which is not an attack on it.

    This rule cost run 12 its claims stage. Told to review the off-switch rule, the stage made
    switches and removed them again, and every removal was refused as tampering until the round had
    been spent arguing with the hook -- 262 refusals, no findings.
    """
    setup()
    for command in ("rm -f /tmp/cc-guard-off",
                    "rm -f /tmp/cc-guard-off /tmp/cc-depth-off",
                    "unlink /tmp/cc-guard-off"):
        decision, _ = run("Bash", {"command": command}, TMP / "none.jsonl")
        assert decision != "deny", command


def test_a_file_that_merely_starts_with_the_name_is_not_the_switch():
    setup()
    for command in ("touch /tmp/cc-guard-off.notes", "rm -rf /tmp/build"):
        decision, _ = run("Bash", {"command": command}, TMP / "none.jsonl")
        assert decision != "deny", command


def test_a_stage_may_ask_the_hook_what_it_would_do():
    """The only admissible evidence about a hook is what the hook printed, and the payload that
    gets it to print has to contain the command being asked about. A rule that reads its own test
    as an attempt refuses the one experiment that could establish anything about it."""
    setup()
    probe = ("echo '{\"tool_name\": \"Bash\", \"tool_input\": {\"command\": "
             "\"touch /tmp/cc-guard-off\"}}' | python3 scripts/cc-context-guard.py")
    decision, why = run("Bash", {"command": probe}, TMP / "none.jsonl")
    assert decision == "allow", why


def test_a_file_that_merely_starts_with_the_switch_name_is_not_the_switch():
    """Also from that review: the Write branch compared the name as a substring, so writing to
    /tmp/cc-guard-off.bak -- a different file -- was refused as tampering."""
    setup()
    decision, _ = run("Write", {"file_path": "/tmp/cc-guard-off.bak", "content": "x"},
                      TMP / "none.jsonl")
    assert decision == "allow"
    decision, _ = run("Write", {"file_path": "/tmp/cc-guard-off", "content": "x"},
                      TMP / "none.jsonl")
    assert decision == "deny"


def test_reading_about_the_off_switch_is_not_tampering():
    """A review of this guard has to be able to grep for the thing it is reviewing."""
    setup()
    for command in ("grep -rn cc-guard-off scripts/",
                    "ls -l /tmp/cc-guard-off",
                    # Refused until a survey stage wrote "the guard itself ran on me", deleted its
                    # test file and fell back to reasoning it could not cite. Writing the result of
                    # a search somewhere is not writing the switch.
                    "grep -rn cc-guard-off scripts/ > /tmp/notes.txt",
                    "rg OFF_SWITCH scripts/cc-context-guard.py"):
        decision, why = run("Bash", {"command": command}, TMP / "none.jsonl")
        assert decision == "allow", (command, why)


def test_the_refusal_says_how_to_exercise_the_rule_it_enforces() -> None:
    """A stage reviewing this rule must be able to test it, and the only permitted way is to send the
    hook a payload. Run 12 spent 262 refusals finding that out by trial and run 20's survey spent half
    its calls on it, because the refusal described the prohibition and not the alternative."""
    decision, why = run("Bash", {"command": "touch /tmp/cc-guard-off"}, "")
    assert decision == "deny", why
    assert "cc-context-guard.py" in why, why
    assert "tool_input" in why, why


def test_a_file_whose_name_merely_ends_with_the_switch_is_not_the_switch():
    """The name was bounded on the right and not on the left, and _VERBS ends in a run of any
    characters, so the switch name matched inside a longer one: `touch /tmp/my-cc-guard-off` was
    refused as tampering with a file it does not name. Found by a review stage probing this rule,
    which is the only reason it is known -- and the refusal cost that stage a round."""
    setup()
    for command in ("touch /tmp/my-cc-guard-off", "touch /tmp/not-cc-depth-off", "mv a b-cc-guard-off"):
        decision, _ = run("Bash", {"command": command}, TMP / "none.jsonl")
        assert decision != "deny", command


def test_a_path_still_reaches_the_switch_itself():
    """Bounding the left side must not stop the rule reaching the file through a directory, which is
    how every real attempt on it is written."""
    setup()
    for command in ("touch /tmp/cc-guard-off", "touch ../tmp/cc-depth-off", "echo x > /tmp/cc-guard-off"):
        decision, _ = run("Bash", {"command": command}, TMP / "none.jsonl")
        assert decision == "deny", command
