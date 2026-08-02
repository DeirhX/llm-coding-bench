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
--dangerously-skip-permissions, so the model sees it and can act on it. Lift it for one session with
`touch /tmp/cc-guard-off`.
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

# Tools that can add thousands of tokens in one call. Write and Edit are deliberately absent: their
# results are a line of confirmation, and forbidding them would leave the model unable to write down
# what it found before stopping.
BULKY = {"Read", "Bash", "WebFetch", "WebSearch", "Grep", "Glob", "NotebookRead"}

# A read that quotes no limit is unbounded, so the only question is how big the file is. 500 lines is
# about 4k tokens of Python, which is a fifth of what one measured session spent re-reading two files.
DEFAULT_MAX_LINES = 500

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


def deny(reason: str):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=98304, help="the runner's real context window")
    ap.add_argument("--framing", type=int, default=4477, help="per-turn overhead the transcript omits")
    ap.add_argument("--stop-pct", type=float, default=80.0)
    ap.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES)
    args = ap.parse_args()

    if OFF_SWITCH.exists():
        allow()

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        allow()

    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    transcript = Path(payload.get("transcript_path") or "")

    records = segment_records(transcript) if transcript.is_file() else []
    used = conversation_tokens(records) + args.framing
    pct = used / args.window * 100 if args.window else 0.0

    finishing = tool == "Bash" and FINISHING.match(tool_input.get("command") or "")
    if pct >= args.stop_pct and tool in BULKY and not finishing:
        deny(
            f"Context guard: the conversation is at {used:,} tokens, {pct:.0f}% of the "
            f"{args.window:,}-token window, and nothing here compacts by itself. Do not gather "
            f"anything further. Write what you have established, and what remains to be done, to "
            f"NOTES.md with Write or Edit, then stop and say the task needs a fresh session. "
            f"git add, git commit and git status remain available so you can land what is done. "
            f"Overrunning the window costs minutes per turn, not seconds. "
            f"(To lift this: touch {OFF_SWITCH})"
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
                    f"search command and read around the hit. (To lift this: touch {OFF_SWITCH})"
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
                    f"them. Reading it again would add the same tokens a second time. "
                    f"(To lift this: touch {OFF_SWITCH})"
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
                        f"with offset and limit, or a search command that prints only matches. "
                        f"(To lift this: touch {OFF_SWITCH})"
                    )

    allow()


if __name__ == "__main__":
    main()
