#!/usr/bin/env python3
"""Inject the task contract, and record it where the gate will look for it.

Runs on `SessionStart` (write `contract.json`) and `UserPromptSubmit` (hand the contract to the
model as `additionalContext`, once). Splitting it that way is not tidiness, it is cache arithmetic.

Why the contract goes at the *end* of a prompt and never into the system block: prefix-cache reuse
is a match from token zero, and the shared head is what makes a fan-out affordable -- a byte
identical head lets sibling sessions restore it in 0.4 s instead of 13.6 s, and a multi-child trie
node is never evicted (LOCAL_AGENT_OPS.md, section 8). Anything per-invocation placed near the front
moves the divergence to the top and there is no shared node to create. `additionalContext` from
`UserPromptSubmit` lands after the head, so adapters may differ freely without costing a prefill.

Why once rather than every turn: the contract is roughly 400 tokens. Re-sending it on every prompt
would buy nothing -- it is already in the conversation, and the model re-reads the whole
conversation each turn anyway -- while adding 400 tokens per turn to a window that cannot compact
itself on this setup.

Fail open like every hook here, and the same kill switch as the gate: `touch /tmp/cc-depth-off`
disables the pair, so a session can always be run ungated without editing settings.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cc_flow
import cc_flowstate
import cc_ledger  # noqa: E402

OFF_SWITCH = Path("/tmp/cc-depth-off")


def emit(event: str, context: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event,
        "additionalContext": context,
    }}))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--adapter", default=os.environ.get("CC_DEPTH_ADAPTER", cc_ledger.DEFAULT_ADAPTER),
                    help="one of: %s" % ", ".join(sorted(cc_ledger.ADAPTERS)))
    args = ap.parse_args()

    if OFF_SWITCH.exists():
        return 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    session = payload.get("session_id") or "nosession"
    root = payload.get("cwd") or os.getcwd()
    event = payload.get("hook_event_name") or "UserPromptSubmit"

    # A person opening a session does not know in advance whether the next hour is a review or a
    # change, and should not have to relaunch to switch. A prompt that names a flow starts it here,
    # which is also the only place that knows the prompt at all.
    started = _flow_from(payload.get("prompt") or "", session, root)
    adapter = cc_flow.FLOW_ADAPTER.get(started, args.adapter) if started else args.adapter
    contract = cc_ledger.contract_for(adapter)

    try:
        directory = cc_ledger.run_dir(session, root)
        directory.mkdir(parents=True, exist_ok=True)
        cc_ledger.write_contract(contract, session, root)
    except OSError:
        return 0        # cannot record it, so do not promise it either

    if event == "SessionStart":
        # The contract is on disk; the model gets it with the first prompt, where it costs no
        # prefix divergence.
        return 0

    marker = directory / "contract-injected"
    if started:
        # A new flow means a new contract, so the injected one has to be re-injected even though
        # this session has seen a contract before.
        marker.unlink(missing_ok=True)
    if marker.exists():
        return 0
    try:
        marker.write_text(contract.adapter + "\n")
    except OSError:
        pass
    context = cc_ledger.contract_markdown(contract)
    if started:
        context += "\n\n" + _flow_briefing(started)
    emit(event, context)
    return 0


FLOW_LINE = re.compile(r"^\s*(?:/)?(?P<flow>review|implement)\b[:\s]+(?P<task>.+)$",
                       re.I | re.M | re.S)


def _flow_from(prompt: str, session: str, root: str) -> str:
    """Start a flow if the prompt asks for one, and return its name.

    Deliberately a prefix and not a classifier: "review" or "implement" as the first word, which is
    what the two slash commands expand to. Guessing from the shape of a sentence would mean a
    session sometimes silently in a flow and sometimes not, and the difference is whether three
    subagents are about to run.
    """
    found = FLOW_LINE.match((prompt or "").strip())
    if not found:
        return ""
    flow = found.group("flow").lower()
    if not cc_flow.flow_for(flow):
        return ""
    cc_flowstate.begin(flow, " ".join(found.group("task").split())[:2000], session, root)
    return flow


def _flow_briefing(flow: str) -> str:
    stages = cc_flow.flow_for(flow) or []
    names = [s.name for s in stages]
    return "\n".join([
        "## This is a %s flow, and it runs in stages" % flow,
        "",
        "Run each of these as a subagent, in this order, waiting for each to report before "
        "launching the next: **%s**." % ", ".join(names),
        "",
        "The first line of a subagent's prompt must be exactly `STAGE: <name>`. You do not need to "
        "write the rest: the stance is substituted for you, identical every time, so that what a "
        "stage does here is the same thing it does when the flow is run as a script. Anything else "
        "you put in the prompt is discarded.",
        "",
        "Each stage is judged when it finishes, and you will be told the verdict. A stage that is "
        "refused can be run again. If a stage the flow depends on is refused, the stages after it "
        "will not start, and the way forward is to satisfy that stage rather than to work around "
        "it.",
        "",
        "Do the work in the stages, not yourself: your job is to launch them in order and to report "
        "what they found.",
    ])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(0)
