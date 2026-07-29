#!/usr/bin/env python3
"""Does either gemma hang or fabricate under NATIVE Ollama tool calling?

Every repohard number in this repo was measured against the bench's own text protocol:
the harness prints ``<arch_tool>`` in the user turn and asks the model to echo the shape
back. Ollama's gemma4 renderer does something entirely different when the request carries
a ``tools`` array -- it emits ``<|tool_call>call:Name{...}<tool_call|>`` and feeds results
back as ``<|tool_response>response:Name{value:...}<tool_response|>``, and a compiled parser
turns the model's output back into structured tool calls.

That is the path an agent CLI takes when it reaches Ollama through a proxy, and it is a
different renderer and a different parser from the one every existing score was measured
on. So the 26B's fabrication -- inventing the result block it should have waited for --
might not reproduce here, or might reproduce and be swallowed by the parser, or might
reproduce and hang. None of those are predictable from the text-protocol results.

The probe runs a multi-step task that needs several tool calls, hands back deliberately
terse results (the shape that provoked fabrication in repohard), and records per turn:
whether a tool call came back, how many tokens it cost, why generation stopped, and
whether the visible text contains a fabricated response marker.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request

HOST = "http://127.0.0.1:11434"

# Markers the gemma4 renderer uses for the harness half of the exchange. If any of these
# show up in generated text, the model is writing the client's lines for it.
FABRICATION = re.compile(r"<\|tool_response>|<tool_response\|>|response:[A-Za-z_]+\{value:")

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the repository.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List the entries of a directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search the repository for a regular expression.",
            "parameters": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    },
]

# A small synthetic repo. The bug is real: charge() retries the whole request on a 5xx
# without carrying the idempotency key, so a retried charge bills twice.
FILES = {
    "ledger/__init__.py": "",
    "ledger/billing.py": (
        "import uuid\n"
        "from .http import post\n"
        "\n"
        "def charge(account_id: str, cents: int) -> dict:\n"
        "    key = str(uuid.uuid4())\n"
        "    for attempt in range(3):\n"
        "        resp = post('/charges', {'account': account_id, 'cents': cents},\n"
        "                    idempotency_key=key if attempt == 0 else None)\n"
        "        if resp['status'] < 500:\n"
        "            return resp\n"
        "    raise RuntimeError('charge failed')\n"
    ),
    "ledger/http.py": (
        "def post(path: str, body: dict, idempotency_key: str | None = None) -> dict:\n"
        "    headers = {}\n"
        "    if idempotency_key:\n"
        "        headers['Idempotency-Key'] = idempotency_key\n"
        "    return _send(path, body, headers)\n"
    ),
}

PROMPT = (
    "The ledger service double-bills customers when the payment provider returns a 5xx. "
    "Find the cause in this repository and tell me the one-line fix. Use the tools to "
    "look around; do not guess at file contents."
)


def dispatch(name: str, args: dict) -> str:
    """Answer a tool call as tersely as a real client would."""
    if name == "list_dir":
        base = (args.get("path") or "").strip("/")
        names = sorted({
            p[len(base):].lstrip("/").split("/")[0]
            for p in FILES
            if p.startswith(base)
        })
        return "\n".join(names) if names else "(empty)"
    if name == "read_file":
        path = (args.get("path") or "").strip("/")
        return FILES.get(path, f"error: no such file: {path}")
    if name == "grep":
        pat = args.get("pattern") or ""
        try:
            rx = re.compile(pat)
        except re.error as exc:
            return f"error: bad pattern: {exc}"
        hits = [
            f"{path}:{i}:{line}"
            for path, text in FILES.items()
            for i, line in enumerate(text.splitlines(), 1)
            if rx.search(line)
        ]
        return "\n".join(hits) if hits else "(no matches)"
    return f"error: unknown tool: {name}"


def chat(model: str, messages: list, timeout: int) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "tools": TOOLS,
        "stream": False,
        "keep_alive": "10m",
    }
    req = urllib.request.Request(
        f"{HOST}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return json.loads(fh.read())


def run(model: str, max_rounds: int, timeout: int) -> dict:
    messages = [{"role": "user", "content": PROMPT}]
    turns = []
    verdict = "completed"
    for rnd in range(1, max_rounds + 1):
        started = time.time()
        try:
            resp = chat(model, messages, timeout)
        except urllib.error.URLError as exc:
            turns.append({"round": rnd, "error": str(exc), "wall_s": round(time.time() - started, 1)})
            verdict = "hang" if "timed out" in str(exc).lower() else "error"
            break
        wall = round(time.time() - started, 1)
        msg = resp.get("message") or {}
        text = msg.get("content") or ""
        thinking = msg.get("thinking") or ""
        calls = msg.get("tool_calls") or []
        turn = {
            "round": rnd,
            "wall_s": wall,
            "eval_count": resp.get("eval_count"),
            "done_reason": resp.get("done_reason"),
            "tool_calls": [c.get("function", {}).get("name") for c in calls],
            "n_tool_calls": len(calls),
            "fabricated": bool(FABRICATION.search(text) or FABRICATION.search(thinking)),
            "content_chars": len(text),
            "thinking_chars": len(thinking),
        }
        turns.append(turn)
        print(f"    r{rnd:<2} {wall:6.1f}s  {str(resp.get('eval_count')):>6} tok  "
              f"end={resp.get('done_reason'):<12} calls={turn['tool_calls'] or '-'}"
              f"{'  FABRICATED' if turn['fabricated'] else ''}", flush=True)

        if turn["done_reason"] not in ("stop", None):
            verdict = f"unclean:{turn['done_reason']}"
            break
        if not calls:
            break

        messages.append(msg)
        for call in calls:
            fn = call.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            messages.append({
                "role": "tool",
                "tool_name": fn.get("name"),
                "content": dispatch(fn.get("name") or "", args),
            })
    else:
        verdict = "round_cap"

    return {
        "model": model,
        "verdict": verdict,
        "rounds": len(turns),
        "total_wall_s": round(sum(t.get("wall_s", 0) for t in turns), 1),
        "total_tokens": sum(t.get("eval_count") or 0 for t in turns),
        "fabricating_turns": sum(1 for t in turns if t.get("fabricated")),
        "unclean_turns": sum(1 for t in turns
                             if t.get("done_reason") not in ("stop", None)),
        "turns": turns,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--max-rounds", type=int, default=15)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", default="results/gemma_native_toolcall_probe.json")
    args = ap.parse_args()

    results = []
    for model in args.models:
        for rep in range(1, args.repeats + 1):
            print(f"\n==== {model}  repeat {rep}/{args.repeats} ====", flush=True)
            res = run(model, args.max_rounds, args.timeout)
            res["repeat"] = rep
            results.append(res)
            print(f"  -> {res['verdict']}  rounds={res['rounds']} "
                  f"tokens={res['total_tokens']} wall={res['total_wall_s']}s "
                  f"fabricating_turns={res['fabricating_turns']}", flush=True)
            with open(args.out, "w") as fh:
                json.dump(results, fh, indent=2)

    print("\n==== summary ====")
    print(f"{'model':28} {'rep':>3} {'verdict':16} {'rounds':>6} {'tokens':>7} {'wall_s':>7} {'fab':>4}")
    for r in results:
        print(f"{r['model']:28} {r['repeat']:>3} {r['verdict']:16} {r['rounds']:>6} "
              f"{r['total_tokens']:>7} {r['total_wall_s']:>7} {r['fabricating_turns']:>4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
