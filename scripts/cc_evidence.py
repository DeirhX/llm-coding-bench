#!/usr/bin/env python3
"""Read a Claude Code session's tool calls out of its transcripts, failures included.

Why not `PostToolUse`: it never fires on a failed tool call. Measured in Phase 0 -- two commands
that exited non-zero and one `Agent` call rejected for a missing field were all present in the
transcript and all absent from the hook log. An evidence trail built on that hook therefore omits
precisely the events worth recording, and reads as a clean run.

Where the evidence is. A session writes
``~/.claude/projects/<munged-cwd>/<session-id>.jsonl`` for itself, and one
``<session-id>/subagents/agent-*.jsonl`` per delegation. Nothing in the parent carries the
subagent's turns -- ``isSidechain`` is present on every event and true on none of them -- so a
recorder that reads only the path a hook hands it sees none of a subagent's work.

Both files use the same shape, which is the one useful simplification here:
  * an assistant event whose ``message.content`` holds a ``tool_use`` block (id, name, input);
  * a later user event holding a ``tool_result`` block with the matching ``tool_use_id``,
    ``is_error``, and the rendered text the model actually saw;
  * on that same user event, ``toolUseResult`` -- a dict for a structured result, a plain string
    beginning ``Error:`` for a failure.

Failure text arrives wrapped in ``<tool_use_error>`` sometimes and bare other times, so the wrapper
is stripped rather than matched on.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Iterator


@dataclass
class ToolCall:
    """One tool invocation and whatever came back, successful or not."""

    agent: str          # "parent", or the agent-* file's id
    tool: str
    call_id: str
    args: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    text: str = ""      # what the model saw, error text included
    detail: Any = None  # structured toolUseResult when there was one
    timestamp: str = ""

    @property
    def target(self) -> str:
        """The argument worth showing in a one-line summary."""
        for key in ("file_path", "pattern", "command", "path", "prompt"):
            if self.args.get(key):
                return str(self.args[key])
        return ""


def iter_events(path: str) -> Iterator[dict]:
    """Yield parsed events, skipping anything unparseable rather than failing the run."""
    try:
        handle = open(os.path.expanduser(path), encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def transcripts_for(transcript_path: str) -> list[tuple[str, str]]:
    """The parent transcript plus every subagent transcript belonging to that session."""
    parent = os.path.expanduser(transcript_path)
    out = [("parent", parent)]
    session_dir = os.path.splitext(parent)[0]
    for sub in sorted(glob.glob(os.path.join(session_dir, "subagents", "agent-*.jsonl"))):
        name = os.path.basename(sub)[len("agent-"):-len(".jsonl")]
        out.append((name, sub))
    return out


def _blocks(event: dict) -> list[dict]:
    content = (event.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, list):
        content = "\n".join(
            c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
        )
    text = str(content or "")
    if text.startswith("<tool_use_error>"):
        text = text[len("<tool_use_error>"):]
        if text.endswith("</tool_use_error>"):
            text = text[: -len("</tool_use_error>")]
    return text.strip()


def collect(transcript_path: str) -> list[ToolCall]:
    """Every tool call in the session, in the order the tool_use blocks appear."""
    calls: list[ToolCall] = []
    for agent, path in transcripts_for(transcript_path):
        by_id: dict[str, ToolCall] = {}
        for event in iter_events(path):
            for block in _blocks(event):
                kind = block.get("type")
                if kind == "tool_use":
                    call = ToolCall(
                        agent=agent,
                        tool=block.get("name", "?"),
                        call_id=block.get("id", ""),
                        args=block.get("input") or {},
                        timestamp=event.get("timestamp", ""),
                    )
                    by_id[call.call_id] = call
                    calls.append(call)
                elif kind == "tool_result":
                    call = by_id.get(block.get("tool_use_id", ""))
                    if call is None:
                        # A result whose call we never saw: keep it rather than drop it.
                        call = ToolCall(agent=agent, tool="?", call_id=block.get("tool_use_id", ""))
                        calls.append(call)
                    call.ok = not bool(block.get("is_error"))
                    call.text = _result_text(block)
                    call.detail = event.get("toolUseResult")
    return calls


def failures(calls: list[ToolCall]) -> list[ToolCall]:
    return [c for c in calls if not c.ok]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("transcript", help="path to <session-id>.jsonl")
    ap.add_argument("--failures-only", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    calls = collect(args.transcript)
    shown = failures(calls) if args.failures_only else calls

    if args.json:
        json.dump([asdict(c) for c in shown], sys.stdout, indent=2)
        print()
        return 0

    agents = sorted({c.agent for c in calls})
    print("%d tool calls, %d failed, across %d transcript(s): %s"
          % (len(calls), len(failures(calls)), len(agents), ", ".join(agents)))
    for c in shown:
        mark = "ok  " if c.ok else "FAIL"
        print("  %s %-9s %-6s %s" % (mark, c.agent[:9], c.tool, c.target[:70]))
        if not c.ok:
            for line in c.text.splitlines()[:3]:
                print("         | %s" % line[:96])
    return 0


if __name__ == "__main__":
    sys.exit(main())
