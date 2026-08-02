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
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
    contract = cc_ledger.contract_for(args.adapter)

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
    if marker.exists():
        return 0
    try:
        marker.write_text(contract.adapter + "\n")
    except OSError:
        pass
    emit(event, cc_ledger.contract_markdown(contract))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(0)
