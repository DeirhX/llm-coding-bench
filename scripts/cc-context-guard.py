#!/usr/bin/env python3
"""A PreToolUse hook that stops a task before it fills the window, and slows the rate it fills at.

Why a hook rather than a prompt: the prompt version of this rule is already in force, and the model
still read benches/pyhard/bench.py twice at ~11,940 tokens a time. Measured on a live session, 82 %
of the conversation was tool results and the ten largest were all whole-file reads. Advice is
advisory; a hook is arithmetic.

Why stopping matters more than compacting: nothing on this setup compacts by itself. The threshold
path needs a feature gate that is never fetched without an Anthropic credential, and the reactive
path needs the API to report an oversized prompt, which Ollama never does -- it grows the KV cache
instead, and the machine pages. Compacting inside the window cost 187 s once; the three compactions
taken above it cost 311, 528 and 666 s. Above the window there is no prefix cache at all and every
turn re-prefills the whole conversation at roughly nine minutes a turn. So the useful intervention
is to end the task while compaction is still cheap.

Three decisions, in order:
  * past the stop threshold, refuse anything that would add bulk, and say what to do instead.
    Write and Edit stay allowed so the model can record its findings before it stops.
  * refuse an unbounded read of a large file, quoting its real line count.
  * refuse a re-read of a file that has not changed since it was last read, quoting when that was.

Failure is always open: any unexpected condition allows the call. A hook that blocks work because it
crashed would be worse than no hook.

Verified end to end against a fake endpoint: the refusal text arrives as the tool_result even under
--dangerously-skip-permissions, so the model sees it and can act on it. Lift it mid-session with
`touch /tmp/cc-guard-off`, which works only in a session launched --liftable: the switch is a file
and a model can make files, so on its own it is not a switch, it is a suggestion.
"""

import argparse
import calendar
import json
import os
import re
import sys
import time
from pathlib import Path

OFF_SWITCH = Path("/tmp/cc-guard-off")
DEPTH_OFF = Path("/tmp/cc-depth-off")
SWITCHES = (OFF_SWITCH, DEPTH_OFF)

# Tools that can add thousands of tokens in one call. Write and Edit are deliberately absent: their
# results are a line of confirmation, and forbidding them would leave the model unable to write down
# what it found before stopping.
BULKY = {"Read", "Bash", "WebFetch", "WebSearch", "Grep", "Glob", "NotebookRead"}

# A read that quotes no limit is unbounded, so the only question is how big the file is. 500 lines is
# about 4k tokens of Python, which is a fifth of what one measured session spent re-reading two files.
DEFAULT_MAX_LINES = 500
# Lines are the wrong unit for some files and the only unit this guard used. A parent read a
# subagent transcript of 231,800 bytes in 67 lines -- around 58,000 tokens, most of a window -- and
# the 500-line rule waved it through, because JSONL puts a whole conversation on one line each.
DEFAULT_MAX_BYTES = 60_000

# Commands that dump a file into the transcript, bypassing the Read guard entirely.
DUMP = re.compile(r"^\s*(?:cat|bat|less|more)\s+(?!.*\|)(\S+)")

# head -n 3000 is the obvious way round a refused Read, so it is treated as one.
HEAD = re.compile(r"^\s*(?:head|tail)\s+(?:-n\s*|-)(\d+)\s+(?!.*\|)(\S+)")

# Finishing a task is not gathering context. These stay available past the threshold, because the
# refusal tells the model to record its findings and stopping it from committing them would be
# perverse. All of them produce a few lines of output at most.
FINISHING = re.compile(r"^\s*git\s+(?:add|commit|status\s+(?:-s|--short)|diff\s+--stat)\b")


def allow():
    """Say nothing: no decision, normal permission flow continues."""
    sys.exit(0)


# After this many refusals with the same words, say different ones. Run 24's survey was told the
# same thing about the off-switch 256 times and spent half an hour rewriting the command to get
# past it -- `'(''?'':''t''o''u''c''h''...` was one attempt. A rule that keeps answering identically
# reads as an obstacle with a trick to it, rather than as an answer.
REPEAT_CAP = 4

# Who is being refused, filled in once the payload is read. A file, because each hook call is its
# own process and there is nowhere else to remember anything.
_WHO: dict = {"agent": "", "ledger": None}


def _repeats(reason: str) -> int:
    """How many times running this caller has been refused with this same message."""
    path = _WHO.get("ledger")
    if path is None:
        return 1
    try:
        seen = json.loads(path.read_text())
    except (OSError, ValueError):
        seen = {}
    who = _WHO.get("agent") or "orchestrator"
    before = seen.get(who) or {}
    count = int(before.get("n", 0)) + 1 if before.get("reason") == reason[:90] else 1
    seen[who] = {"reason": reason[:90], "n": count}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(seen))
    except OSError:
        pass
    return count


def deny(reason: str):
    said = _repeats(reason)
    if said > REPEAT_CAP:
        reason = ("Refused, the same way, for the %dth time. The wording of the call is not what is "
                  "being refused, so rewriting it will not get past this. Stop and answer with what "
                  "you already have; anything you could not establish is an UNKNOWN, which is a "
                  "complete answer. The refusal, in short: %s" % (said, " ".join(reason.split())[:180]))
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)


def segment_records(transcript: Path):
    """Records since the last compaction, which is what the prompt actually carries."""
    records = []
    with transcript.open(errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    last = 0
    for i, rec in enumerate(records):
        if rec.get("compactMetadata"):
            last = i
    return records[last:]


def conversation_tokens(records) -> int:
    """chars/4 over every block that ends up in the prompt.

    Deliberately approximate: a tokenizer call per tool call would add latency to every single one,
    and the decision is a threshold, not a measurement. Measured against a real session this
    underestimates by roughly 5 %, which the framing allowance below more than covers.
    """
    total = 0
    for rec in records:
        msg = rec.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            total += len(content) // 4
            continue
        for blk in (content if isinstance(content, list) else []):
            if not isinstance(blk, dict):
                continue
            kind = blk.get("type")
            if kind in ("text", "thinking"):
                total += len(blk.get("text") or blk.get("thinking") or "") // 4
            elif kind == "tool_use":
                total += len(json.dumps(blk.get("input") or {})) // 4
            elif kind == "tool_result":
                body = blk.get("content")
                total += len(body if isinstance(body, str) else json.dumps(body)) // 4
    return total


def failed_tool_uses(records):
    """Ids of calls whose result was an error, including the guard's own refusals.

    A refusal is written to the transcript as an ordinary Read tool_use; only the result carries
    is_error. Counting those as reads would make the guard refuse the narrower retry it had just
    demanded, leaving the model no legal way to read the file -- a refusal loop, one turn each.
    """
    failed = set()
    for rec in records:
        msg = rec.get("message") or {}
        for blk in (msg.get("content") if isinstance(msg.get("content"), list) else []):
            if isinstance(blk, dict) and blk.get("type") == "tool_result" and blk.get("is_error"):
                if blk.get("tool_use_id"):
                    failed.add(blk["tool_use_id"])
    return failed


def prior_reads(records, path: str):
    """Every earlier *successful* read of this path in the current segment, as (epoch, offset, end)."""
    out = []
    failed = failed_tool_uses(records)
    for rec in records:
        msg = rec.get("message") or {}
        for blk in (msg.get("content") if isinstance(msg.get("content"), list) else []):
            if not isinstance(blk, dict) or blk.get("type") != "tool_use":
                continue
            if blk.get("name") != "Read" or blk.get("id") in failed:
                continue
            args = blk.get("input") or {}
            if args.get("file_path") != path:
                continue
            ts = rec.get("timestamp") or ""
            try:
                # transcript timestamps are UTC; mktime would read them as local and place every
                # read an hour or two earlier than it happened, which silently disables this check
                epoch = calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                epoch = 0.0
            offset = int(args.get("offset") or 1)
            limit = args.get("limit")
            end = offset + int(limit) - 1 if limit else float("inf")
            out.append((epoch, offset, end))
    return out


def line_count(path: Path) -> int:
    with path.open("rb") as fh:
        return sum(1 for _ in fh)


_SLEEP = re.compile(r"(?:^|[;&|(){}\n])\s*sleep\s+(\d+)")
_HEREDOC = re.compile(r"<<-?\s*'?\"?(\w+)'?\"?.*?^\1$", re.S | re.M)
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"", re.S)


_LEDGER_HEAD = re.compile(r"^\W{0,4}(CLAIM|UNKNOWN|EVIDENCE|QUOTE)\b")


def is_ledger(text: str) -> bool:
    """True if this file is a ledger rather than code: it opens on a ledger header and cites.

    A claims stage wrote a well-formed ledger of four findings to claims.jsonl and put a prose
    summary in its reply. The gate reads the reply, so all four were judged as citing nothing, and
    the stage was refused for work it had actually done. Saying so in the contract did not stop it
    happening again the next run, so the write is refused where the model can still act on it.

    Deliberately narrow: this repo's own sources quote ledger headers all over, and an implement
    stage editing cc_ledger.py must not trip on them. Only a file that *begins* as a ledger counts.
    """
    body = text.lstrip()
    return bool(body and _LEDGER_HEAD.match(body) and "EVIDENCE" in text)


def naps_in(command: str) -> list[int]:
    """Seconds the shell would actually spend sleeping, ignoring sleeps that are only text.

    The first version of this matched the word sleep and a number anywhere in the command, and a
    stage asked to review this very rule was denied three times for saying `sleep 180` in a heredoc
    and inside `python3 -c`, where no shell was going to sleep. A rule that refuses the attempt to
    test it is worse than no rule: it looks like the tool is broken, and the stage spends its budget
    working around the guard instead of on the question.
    """
    bare = _QUOTED.sub(" ", _HEREDOC.sub(" ", command))
    return [int(m.group(1)) for m in _SLEEP.finditer(bare)]


# A probe of the hook, which is the one way to establish what the hook does. The payload it feeds in
# has to contain the very command being asked about, so a rule that reads its own test as an attempt
# refuses the only admissible evidence about itself -- and did: a survey stage wrote "the guard
# itself ran on me", deleted its test file and fell back to reasoning it could not cite.
_PROBE = re.compile(r"cc-(?:context-guard|depth-gate|flow-guard|depth-contract)\.py")

# Written as "what would this command do to that path", not "does this command mention that path".
# The mention version refused `grep -rn cc-guard-off scripts/ > notes.txt`, which reads the rule and
# writes somewhere else entirely, and a guard that refuses reading is a guard nobody can review.
# Making an off-switch is what the rule is for. Removing one is the opposite: it puts the guard
# back. Refusing that cost run 12 its claims stage, which was reviewing this very rule, probed
# it honestly, and was then refused 262 times for trying to clean up after itself.
_VERBS = r"(?:touch|mv|cp|dd|tee|install|ln|shred|truncate)\b[^;|&\n]*"
_CALLS = r"(?:open|Path|write_text|writeFile)\s*\(\s*['\"]?"


def tampers(command: str) -> bool:
    """True if this command would create or remove a guard's off-switch.

    A stage that wants past a refusal can make the file some way this does not match -- by building
    the name out of two strings, or through a variable -- which is why the switch needs the launch
    flag and not just this. What this buys is that the obvious attempt costs a turn and is told what
    to do instead, rather than appearing to work.
    """
    if _PROBE.search(command) and ("tool_input" in command or "tool_name" in command):
        return False
    for switch in SWITCHES:
        # The directory is optional because a stage writes the path any way it likes -- /tmp/x,
        # ../tmp/x, or x from /tmp -- and the file is what matters.
        # Both ends are bounded. The right one was, the left was not, and _VERBS ends in a run of
        # any characters -- so the name matched inside a longer one and `touch /tmp/my-cc-guard-off`
        # was refused as tampering with a file it does not name. Found by a review stage probing this
        # very rule, which is the only reason it is known. A `/` is not a name character, so a path
        # still reaches the switch.
        name = (r"(?:[^\s'\";|&]*/)?(?<![\w.-])" + re.escape(switch.name) + r"(?![\w.-])")
        if re.search(_VERBS + name, command):
            return True
        if re.search(r">>?\s*['\"]?" + name, command):
            return True
        if re.search(_CALLS + name, command):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=98304, help="the runner's real context window")
    ap.add_argument("--framing", type=int, default=4477, help="per-turn overhead the transcript omits")
    ap.add_argument("--stop-pct", type=float, default=80.0)
    ap.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES)
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                    help="largest file a single Read may take whole, in bytes")
    # An interactive session can write its findings down and be resumed. A pipeline stage has
    # read-only tools and one reply, so telling it to write NOTES.md names a tool it does not have,
    # and a model that cannot obey an instruction tends to ignore the sentence around it too.
    ap.add_argument("--stop-advice", choices=("notes", "answer"), default="notes")
    ap.add_argument("--deny", default="", help="comma-separated tools to refuse outright")
    ap.add_argument("--max-sleep", type=int, default=30,
                    help="longest sleep a Bash command may contain, in seconds")
    args = ap.parse_args()

    # The file alone does nothing. A stage that wants past a refusal can create a file -- one did,
    # and left the guard off for every session that followed -- and no amount of pattern-matching on
    # shell commands closes that: `python3 -c "open('/tmp/cc-guard'+'-off','w')"` never says the
    # name. What a stage cannot do is change the environment a hook is spawned into, because each
    # hook is started fresh by the client from the client's environment, so the lever lives there.
    # The pattern rule below stays as a tripwire: it costs the stage a turn and says what to do
    # instead, rather than letting it believe it has succeeded.
    if OFF_SWITCH.exists() and os.environ.get("CC_GUARD_LIFTABLE") == "1":
        allow()

    _WHO["ledger"] = Path("/tmp/cc-refusals-%s.json"
                          % (os.environ.get("CLAUDE_SESSION_ID") or "session"))

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        allow()

    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    transcript = Path(payload.get("transcript_path") or "")
    _WHO["agent"] = str(payload.get("agent_id") or "")
    _WHO["ledger"] = Path("/tmp/cc-refusals-%s.json"
                          % (payload.get("session_id") or "session"))

    # Only one PreToolUse refusal reaches the model when two hooks refuse the same call, and which
    # one is neither documented nor stable -- the order was changed once already to fix exactly this
    # and fixed nothing. So the call budget is said here too, in the same words the flow guard uses.
    # Run 24's survey spent 280 calls against a budget of 60 and was refused for 220 of them without
    # ever being told why: what it saw, every time, was this guard talking about something else.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import cc_flowstate
        state = cc_flowstate.peek(cc_flowstate.session_of(payload),
                                  payload.get("cwd") or os.getcwd())
        for stage in cc_flowstate.running(state):
            over = cc_flowstate.overspent(state, stage)
            if over > 0:
                deny("You have spent %d tool calls past the budget of the %s stage. Stop reading "
                     "and write your answer now from what you have already seen. Anything you "
                     "could not establish is an UNKNOWN, which is a complete answer here -- going "
                     "round the files again is not." % (over, stage))
    except (ImportError, OSError, ValueError):
        pass

    # --allowed-tools pre-approves; it does not forbid, and under
    # --dangerously-skip-permissions nothing does. A stage told to judge a change made nine
    # successful edits to the very files it was judging, on a tool list that named only Read, Grep,
    # Glob and Bash. A PreToolUse denial is the one mechanism measured to hold here.
    # A stage put a test run in the background and then polled it with `sleep 180 && tail`, twice,
    # and spent twenty minutes of a fifty-minute budget waiting for itself. The suite it was waiting
    # on takes under three seconds in the foreground. Nothing here needs to wait minutes for
    # anything, so a long sleep is always the wrong answer rather than sometimes.
    naps = naps_in(tool_input.get("command") or "")
    if tool == "Bash" and any(n > args.max_sleep for n in naps):
        deny("Do not sleep for %ds. Run the command in the foreground and wait for it there: "
             "polling a background job costs the whole wait and tells you nothing the exit status "
             "would not. If something really does take minutes, say so and answer without it."
             % max(naps))

    if tool == "Bash" and tampers(tool_input.get("command") or ""):
        # A stage reviewing this rule has to exercise it, and the only way that is permitted is to
        # send the hook a payload -- which the rule allows and nothing said so. Run 12 spent 262
        # refusals discovering that by trial; run 20's survey spent half its calls on the same thing.
        # So the refusal now names the one form that works.
        deny("The guards' off-switches are the operator's, not yours, and turning one off to get past "
             "a refusal is not a way round it: the refusal is the instruction. To test this rule "
             "rather than trip it, send the hook the payload instead of running the command -- "
             "echo '{\"tool_name\": \"Bash\", \"tool_input\": {\"command\": \"touch "
             "/tmp/cc-guard-off\"}}' | python3 scripts/cc-context-guard.py -- which is allowed, and "
             "prints the refusal you are asking about. Otherwise do what the refusal said, or say in "
             "your answer that you could not and why.")
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit") and any(
            Path(tool_input.get("file_path") or "").name == s.name for s in SWITCHES):
        deny("The guards' off-switches are the operator's, not yours. Do what the refusal said, or "
             "say in your answer that you could not and why.")

    written = tool_input.get("content") or tool_input.get("new_string") or ""
    if tool in ("Write", "Edit", "MultiEdit") and is_ledger(written):
        deny("Nothing you write to a file is read here -- your report is the message you finish "
             "with. Put these CLAIM/EVIDENCE/QUOTE blocks in your reply instead, in full, and do "
             "not summarise them in prose: the summary is what gets judged, and a summary cites "
             "nothing.")

    denied = {t.strip() for t in args.deny.split(",") if t.strip()}
    if tool in denied:
        deny("This stage may not use %s. It is judging a change it did not make, and a judge that "
             "can edit the code is not reporting on it. Read, run and measure instead; if the "
             "change is wrong, say so and say why." % tool)

    records = segment_records(transcript) if transcript.is_file() else []
    used = conversation_tokens(records) + args.framing
    pct = used / args.window * 100 if args.window else 0.0

    finishing = tool == "Bash" and FINISHING.match(tool_input.get("command") or "")
    if pct >= args.stop_pct and tool in BULKY and not finishing:
        if args.stop_advice == "answer":
            advice = ("Do not gather anything further. Answer now with what you have established, "
                      "and put everything you did not get to under UNKNOWN. An answer that stops "
                      "early and says so is complete; one that runs the window out is not.")
        else:
            advice = ("Do not gather anything further. Write what you have established, and what "
                      "remains to be done, to NOTES.md with Write or Edit, then stop and say the "
                      "task needs a fresh session. git add, git commit and git status remain "
                      "available so you can land what is done.")
        deny(
            f"Context guard: the conversation is at {used:,} tokens, {pct:.0f}% of the "
            f"{args.window:,}-token window, and nothing here compacts by itself. {advice} "
            f"Overrunning the window costs minutes per turn, not seconds."
        )

    if tool == "Read":
        path_str = tool_input.get("file_path") or ""
        path = Path(path_str)
        if not path.is_file():
            allow()

        # A limit larger than the cap is the same thing as no limit: the first version of this
        # guard checked only for a missing limit, so "offset 1, limit 5000" walked straight past it
        # and read the file whole -- which is precisely what a model does when told to use a limit.
        requested = tool_input.get("limit")
        wants_everything = not requested or int(requested) > args.max_lines
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if wants_everything and size > args.max_bytes:
            deny(
                f"Context guard: {path.name} is {size:,} bytes in {line_count(path):,} lines, "
                f"roughly {size // 4:,} tokens, and reading it whole would spend most of the "
                f"window on one call. Long lines are why the line limit did not catch this. Use a "
                f"search command to find what you need, or read a slice with offset and limit."
            )
        if wants_everything:
            try:
                lines = line_count(path)
            except OSError:
                allow()
            if lines > args.max_lines:
                asked = (f"a limit of {int(requested):,} lines" if requested
                         else "an unbounded read")
                deny(
                    f"Context guard: {path.name} is {lines:,} lines, roughly "
                    f"{path.stat().st_size // 4:,} tokens, and {asked} of it stays in "
                    f"the conversation for the rest of the session. Read at most "
                    f"{args.max_lines} lines with offset and limit, or find what you need with a "
                    f"search command and read around the hit."
                )

        offset = int(tool_input.get("offset") or 1)
        limit = tool_input.get("limit")
        end = offset + int(limit) - 1 if limit else float("inf")
        try:
            mtime = path.stat().st_mtime
        except OSError:
            allow()
        for when, prev_offset, prev_end in prior_reads(records, path_str):
            covers = prev_offset <= offset and prev_end >= end
            if covers and when and mtime <= when:
                stamp = time.strftime("%H:%M:%S", time.localtime(when))
                deny(
                    f"Context guard: you already read {path.name} at {stamp} and it has not "
                    f"changed since. Its contents are still above in this conversation -- use "
                    f"them. Reading it again would add the same tokens a second time."
                )

    if tool == "Bash":
        command = tool_input.get("command") or ""
        match = DUMP.match(command)
        head = HEAD.match(command)
        if head and int(head.group(1)) > args.max_lines:
            match = head
        elif head:
            match = None
        if match:
            target = Path(os.path.expanduser(match.groups()[-1]))
            if target.is_file():
                try:
                    lines = line_count(target)
                except OSError:
                    allow()
                if lines > args.max_lines:
                    deny(
                        f"Context guard: that dumps {target.name}, {lines:,} lines, into the "
                        f"conversation, which is what the Read limit exists to prevent. Use Read "
                        f"with offset and limit, or a search command that prints only matches."
                    )

    allow()


if __name__ == "__main__":
    main()
