#!/usr/bin/env python3
"""How close a live run is to the end of the window, from the runner's own token counts.

Three runs died of a prompt too long for the context, and each time the only record of how close the
preceding turns had come was the error that ended them. The proxy now writes the tokeniser's count of
every request it completes, so the question "is this run about to die" has an answer while there is
still time to act on it.

The proxy logs no client identity -- it cannot, the requests carry none -- so agents are told apart by
the shape of what they send: a parent has the flow tools and the contract in its system prompt, a
subagent has neither. That is enough to separate them, and it is all that is available.

    python3 scripts/context_watch.py --window 131072
    python3 scripts/context_watch.py --since 30m --every 60
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import defaultdict


def parse_since(said: str) -> float:
    """Seconds, from `90`, `30m` or `2h`."""
    match = re.fullmatch(r"(\d+)([smh]?)", said.strip())
    if not match:
        raise SystemExit("--since wants a number of seconds, or 30m, or 2h -- not %r" % said)
    scale = {"": 1, "s": 1, "m": 60, "h": 3600}[match.group(2)]
    return int(match.group(1)) * scale


def turns(path: str, since: float) -> list[dict]:
    """Completed requests, each with the shape of what was sent and what it cost.

    A request and its completion are separate lines, and the proxy serialises them behind one slot,
    so pairing the nearest preceding request with each `done` is exact rather than a guess.
    """
    if not os.path.isfile(path):
        raise SystemExit("no proxy log at %s -- start the proxy with --log" % path)
    paired: list[dict] = []
    asked: dict | None = None
    for line in open(path, errors="replace"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if float(row.get("t", 0)) < since:
            continue
        if row.get("event") == "request":
            asked = row
        elif row.get("event") == "done" and asked is not None:
            paired.append({"tools": asked.get("tools"), "system": asked.get("system_chars"),
                           "messages": asked.get("messages"), "t": float(row.get("t", 0)),
                           "input": row.get("input_tokens") or 0,
                           "output": row.get("output_tokens") or 0,
                           "ms": row.get("ms") or 0})
            asked = None
        elif row.get("event") in ("upstream_error", "client_gone"):
            paired.append({"tools": asked.get("tools") if asked else None,
                           "system": asked.get("system_chars") if asked else None,
                           "messages": asked.get("messages") if asked else None,
                           "t": float(row.get("t", 0)), "failed": row.get("event"),
                           "detail": str(row.get("detail") or "")[:160],
                           "input": 0, "output": 0, "ms": 0})
            asked = None
    return paired


def report(paired: list[dict], window: int) -> None:
    if not paired:
        print("nothing has gone through the proxy in this period")
        return
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for turn in paired:
        groups[(turn["tools"], turn["system"])].append(turn)

    for shape, seen in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        tools, system = shape
        # The parent is the one holding the flow tools; a stage has the lean set and a shorter system
        # prompt. A request with neither tools nor a system prompt is the client compacting -- it
        # sends the conversation as one message and asks for a summary -- and seeing those is how you
        # know the declared ceiling is doing its job instead of being quietly ignored.
        if not tools and not system and max((t["input"] for t in seen), default=0) > 2000:
            who = "compact"
        else:
            who = "parent" if (tools or 0) > 10 else "stage"
        costs = [t["input"] for t in seen if t["input"]]
        peak = max(costs) if costs else 0
        last = costs[-1] if costs else 0
        failed = [t for t in seen if t.get("failed")]
        print("%-7s tools=%-3s system=%-6s turns=%-4d" % (who, tools, system, len(seen)), end="")
        if peak:
            print("  peak %6s (%3.0f%% of window)  last %6s"
                  % ("{:,}".format(peak), peak / window * 100, "{:,}".format(last)), end="")
        print()
        if failed:
            for turn in failed[-2:]:
                print("        %s at %s: %s" % (turn["failed"],
                                                time.strftime("%H:%M:%S", time.localtime(turn["t"])),
                                                turn["detail"]))
        # Growth per turn is what says whether the run reaches the end before the work does.
        if len(costs) >= 3:
            climb = (costs[-1] - costs[0]) / max(1, len(costs) - 1)
            room = window - last
            print("        growing %s tokens a turn; %s tokens of room, about %s turns of it"
                  % ("{:+,}".format(int(climb)), "{:,}".format(room),
                     "%d" % (room / climb) if climb > 0 else "no limit at this rate"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", default="/tmp/anthropic-proxy.jsonl")
    ap.add_argument("--window", type=int, default=131072, help="the runner's real context window")
    ap.add_argument("--since", default="2h", help="how far back to look: 90, 30m, 2h")
    ap.add_argument("--every", type=int, default=0, help="keep reporting, this many seconds apart")
    args = ap.parse_args()
    while True:
        cutoff = time.time() - parse_since(args.since)
        print("-- %s" % time.strftime("%H:%M:%S"))
        report(turns(args.log, cutoff), args.window)
        if not args.every:
            return 0
        time.sleep(args.every)


if __name__ == "__main__":
    raise SystemExit(main())
