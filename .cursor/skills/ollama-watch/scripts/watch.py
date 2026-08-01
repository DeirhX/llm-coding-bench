#!/usr/bin/env python3
"""Follow the Ollama log and report only the things worth interrupting for.

The log is far too chatty to read live, and the interesting events are rare and
easy to miss in the noise. This reports five of them:

  restore failure   "failed to restore" or "freeing all caches": the saved prefix
                    cache was discarded, so the next turn pays a full prefill
  cold prefill      a large prompt processed with little or no cache reuse, which
                    is minutes of waiting rather than seconds
  loop              the same prompt total restarted from zero more than once, the
                    signature of a client retrying or a prompt above the window
  near the window   a prompt within a few percent of the model's context length.
                    Ollama truncates silently past that point rather than erroring,
                    so this is the last warning before context is quietly lost
  swap              swap crossing a threshold, which precedes throughput collapse

Runs until interrupted. Meant for a screen session during unattended work.

usage: watch.py [--cold-tokens 20000] [--swap-gb 8] [--interval 3]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

HOST = "http://127.0.0.1:11434"
LOG = Path.home() / ".ollama/logs/server.log"

PROGRESS = re.compile(r"Prompt processing progress.*?processed=(\d+)\s+total=(\d+)")
CACHE_HIT = re.compile(r"cache hit.*?total=(\d+)\s+matched=(\d+)")
RESTORE = re.compile(r"failed to restore|freeing all caches", re.I)
DIED = re.compile(r"exited unexpectedly|error loading llama server", re.I)
TRUNCATED = re.compile(r"truncated\s*=\s*[1-9]|truncated=true", re.I)


def alert(kind: str, message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {kind:<14} {message}", flush=True)


def swap_gb() -> float:
    out = subprocess.run(
        ["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True
    ).stdout
    if m := re.search(r"used\s*=\s*([\d.]+)([MG])", out):
        return float(m.group(1)) * (1.024 if m.group(2) == "G" else 0.001024)
    return 0.0


def windows() -> dict[str, int]:
    """Context length per resident model, for the near-the-window check."""
    try:
        with urllib.request.urlopen(f"{HOST}/api/ps", timeout=5) as fh:
            models = json.load(fh).get("models") or []
    except Exception:  # noqa: BLE001 - absence is not an error here
        return {}
    out = {}
    for m in models:
        name = m.get("name") or m.get("model") or "?"
        ctx = m.get("context_length") or (m.get("details") or {}).get("context_length")
        if ctx:
            out[name] = int(ctx)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cold-tokens", type=int, default=20000,
                    help="prefill size above which poor cache reuse is reported")
    ap.add_argument("--swap-gb", type=float, default=8.0)
    ap.add_argument("--interval", type=float, default=3.0)
    ap.add_argument("--log", default=str(LOG))
    args = ap.parse_args()

    path = Path(args.log)
    if not path.is_file():
        print(f"no log at {path}", file=sys.stderr)
        return 1

    pos = path.stat().st_size
    print(f"watching {path} from byte {pos:,}", flush=True)
    print(f"cold prefill above {args.cold_tokens:,} tokens, swap above {args.swap_gb} GB", flush=True)

    cur_total = 0
    cur_processed = 0
    restarts = 0
    reported_cold = False
    last_match = 0
    swap_flagged = False
    win = windows()
    win_checked = time.time()
    near_flagged: set[int] = set()

    while True:
        time.sleep(args.interval)

        size = path.stat().st_size
        if size < pos:  # rotated
            alert("log", "rotated, following the new file")
            pos = 0
        if size > pos:
            with path.open("rb") as fh:
                fh.seek(pos)
                chunk = fh.read(size - pos).decode("utf-8", "replace")
            pos = size

            for line in chunk.split("\n"):
                if RESTORE.search(line):
                    alert("RESTORE FAIL", line.strip()[:120])
                elif DIED.search(line):
                    alert("RUNNER DIED", line.strip()[:120])
                elif TRUNCATED.search(line):
                    alert("TRUNCATED", "context was silently cut: " + line.strip()[:100])
                elif m := CACHE_HIT.search(line):
                    last_match = int(m.group(2))
                elif m := PROGRESS.search(line):
                    processed, total = int(m.group(1)), int(m.group(2))
                    if total != cur_total:
                        cur_total, cur_processed = total, processed
                        restarts = 0
                        reported_cold = False
                        reuse = last_match / total if total else 0
                        if total >= args.cold_tokens and reuse < 0.5:
                            alert("COLD PREFILL",
                                  f"{total:,} tokens with {reuse * 100:.0f}% reused")
                            reported_cold = True
                        for name, ctx in win.items():
                            if total > ctx * 0.95 and total not in near_flagged:
                                near_flagged.add(total)
                                over = "ABOVE" if total > ctx else "within 5% of"
                                alert("NEAR WINDOW",
                                      f"{total:,} tokens is {over} {name}'s {ctx:,}")
                    elif processed < cur_processed:
                        restarts += 1
                        cur_processed = processed
                        if restarts >= 2:
                            alert("LOOP",
                                  f"{total:,}-token prompt restarted {restarts} times; "
                                  "stop the client, not the model")
                        else:
                            alert("RESTART", f"{total:,}-token prefill began again")
                    else:
                        cur_processed = processed
                        if not reported_cold and total >= args.cold_tokens:
                            reported_cold = True

        used = swap_gb()
        if used >= args.swap_gb and not swap_flagged:
            swap_flagged = True
            alert("SWAP", f"{used:.1f} GB in use, throughput will suffer")
        elif used < args.swap_gb * 0.8 and swap_flagged:
            swap_flagged = False
            alert("swap", f"back down to {used:.1f} GB")

        if time.time() - win_checked > 60:
            win = windows() or win
            win_checked = time.time()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped")
