#!/usr/bin/env python3
"""What is Ollama doing right now, and is it stuck?

Combines /api/ps with the server log, because neither answers the question alone.
/api/ps says a model is resident but not whether it is working; the log shows work
but not what is loaded. The verdict line at the end is the point of the script.

Distinguishing busy from stuck is the hard part, so it is done from evidence
rather than a guess:

  prefilling   progress lines are advancing; the rate and remaining time are
               computed from the current burst, so "silent for two minutes" can
               be told apart from "will finish in forty seconds"
  decoding     no progress lines, but the runner is burning CPU or the model's
               expiry sits in the past. Neither signal alone is enough: a runner
               decoding at 203% CPU still reported 480 minutes of keep-alive
  idle         resident with a future expiry and nothing in flight
  looping      the same prompt total restarted from zero more than once, meaning a
               client is retrying or the prompt is too long to cache. That is nine
               minutes per turn, forever, until the client is stopped

Two traps this script exists to avoid. The log line "truncated = 0" says nothing
was truncated, so a naive search for "truncat" reports trouble on every healthy
request. And a runner that died an hour ago is not a current problem, so trouble
is reported with its age and only when recent.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HOST = "http://127.0.0.1:11434"
LOG = Path.home() / ".ollama/logs/server.log"
TAIL_BYTES = 400_000
BUSY_SECONDS = 90        # progress older than this is not the current burst
DONE_SLACK = 64          # within this many tokens of the total, prefill is over
TROUBLE_MINUTES = 15     # older trouble is history, not a diagnosis
BUSY_CPU = 40.0          # runner cpu above this means work, whatever expiry says

TS = re.compile(r"time=(\S+?)\s")
PROGRESS = re.compile(r"Prompt processing progress.*?processed=(\d+)\s+total=(\d+)")
CACHE_HIT = re.compile(r"cache hit.*?total=(\d+)\s+matched=(\d+)")
TIMING = re.compile(r"(prompt eval|eval) (?:count|rate).*")
TROUBLE = re.compile(
    r"failed to restore|freeing all caches|exited unexpectedly|out of memory"
    r"|truncated\s*=\s*[1-9]|truncated=true",
    re.I,
)


@dataclass
class Burst:
    total: int
    first_at: datetime
    last_at: datetime
    first_processed: int
    last_processed: int

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.last_processed)

    @property
    def rate(self) -> float:
        span = (self.last_at - self.first_at).total_seconds()
        moved = self.last_processed - self.first_processed
        return moved / span if span > 0 and moved > 0 else 0.0

    @property
    def eta_seconds(self) -> float:
        return self.remaining / self.rate if self.rate else 0.0


@dataclass
class LogView:
    burst: Burst | None = None
    cache: tuple[int, int] | None = None
    trouble: list[tuple[datetime | None, str]] = field(default_factory=list)
    timings: list[str] = field(default_factory=list)
    restarts: int = 0


def _stamp(line: str) -> datetime | None:
    m = TS.search(line)
    if not m:
        return None
    try:
        return datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
    except ValueError:
        return None


def read_log(path: Path = LOG) -> LogView:
    view = LogView()
    if not path.is_file():
        return view
    size = path.stat().st_size
    with path.open("rb") as fh:
        fh.seek(max(0, size - TAIL_BYTES))
        lines = fh.read().decode("utf-8", "replace").split("\n")[1:]

    for line in lines:
        if m := PROGRESS.search(line):
            processed, total = int(m.group(1)), int(m.group(2))
            at = _stamp(line) or datetime.now(timezone.utc)
            b = view.burst
            if b is None or b.total != total:
                view.burst = Burst(total, at, at, processed, processed)
            elif processed < b.last_processed:
                # Same prompt, counter went backwards: the work started again.
                view.restarts += 1
                view.burst = Burst(total, at, at, processed, processed)
            else:
                b.last_at, b.last_processed = at, processed
        elif m := CACHE_HIT.search(line):
            view.cache = (int(m.group(1)), int(m.group(2)))
        elif TROUBLE.search(line):
            view.trouble.append((_stamp(line), line.strip()[:150]))
        elif TIMING.search(line):
            view.timings.append(line.strip()[-110:])
    return view


def resident() -> list[dict]:
    try:
        with urllib.request.urlopen(f"{HOST}/api/ps", timeout=10) as fh:
            return json.load(fh).get("models") or []
    except Exception as exc:  # noqa: BLE001 - any failure means "cannot tell"
        print(f"api/ps unreachable: {exc}", file=sys.stderr)
        return []


def runners() -> list[tuple[float, float]]:
    """(cpu percent, resident gigabytes) for each live runner subprocess."""
    out = subprocess.run(
        ["ps", "-Ao", "pcpu,rss,command"], capture_output=True, text=True
    ).stdout
    found = []
    for line in out.split("\n"):
        if "ollama runner" not in line or " grep " in line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            found.append((float(parts[0]), int(parts[1]) / 1048576))
    return found


def memory() -> tuple[float, float, float]:
    """(free, available, swap used) in gigabytes.

    Free pages alone badly understate what a large model can still get, because
    macOS counts reclaimable file cache separately. Available adds the pages the
    kernel would hand over on demand.
    """
    pages = {}
    for line in subprocess.run(["vm_stat"], capture_output=True, text=True).stdout.split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            digits = val.strip().rstrip(".")
            if digits.isdigit():
                pages[key.strip()] = int(digits)
    page = 16384
    free = pages.get("Pages free", 0) * page / 1e9
    available = (
        pages.get("Pages free", 0)
        + pages.get("Pages inactive", 0)
        + pages.get("Pages speculative", 0)
        + pages.get("Pages purgeable", 0)
    ) * page / 1e9
    swap = subprocess.run(
        ["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True
    ).stdout
    used = 0.0
    if m := re.search(r"used\s*=\s*([\d.]+)([MG])", swap):
        used = float(m.group(1)) * (1.024 if m.group(2) == "G" else 0.001024)
    total = 0.0
    if m := re.search(r"total\s*=\s*([\d.]+)([MG])", swap):
        total = float(m.group(1)) * (1.024 if m.group(2) == "G" else 0.001024)
    wired = pages.get("Pages wired down", 0) * page / 1e9
    return free, available, used, total, wired


def minutes_left(model: dict) -> float | None:
    raw = (model.get("expires_at") or "").replace("Z", "+00:00")
    try:
        gap = datetime.fromisoformat(raw) - datetime.now(timezone.utc)
    except ValueError:
        return None
    return gap.total_seconds() / 60


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log", default=str(LOG))
    args = ap.parse_args()

    models = resident()
    view = read_log(Path(args.log))
    live = runners()
    free_gb, avail_gb, swap_gb, swap_total_gb, wired_gb = memory()
    now = datetime.now(timezone.utc)

    busy_expiry = any(
        (left := minutes_left(m)) is not None and left <= 0 for m in models
    )
    busy_cpu = any(cpu > BUSY_CPU for cpu, _ in live)
    burst = view.burst
    age = (now - burst.last_at).total_seconds() if burst else None
    prefilling = bool(
        burst and age is not None and age < BUSY_SECONDS and burst.remaining > DONE_SLACK
    )
    recent_trouble = [
        (at, text) for at, text in view.trouble
        if at is None or (now - at).total_seconds() < TROUBLE_MINUTES * 60
    ]

    if prefilling:
        verdict = "PREFILLING"
    elif (busy_expiry or busy_cpu) and live:
        verdict = "BUSY (generating)"
    elif models:
        verdict = "IDLE (resident)"
    elif live:
        verdict = "RUNNER UP, NOTHING RESIDENT"
    else:
        verdict = "NOTHING LOADED"

    looping = view.restarts > 1

    if args.json:
        print(json.dumps({
            "verdict": verdict,
            "looping": looping,
            "restarts": view.restarts,
            "busy_cpu": busy_cpu,
            "free_gb": round(free_gb, 1),
            "available_gb": round(avail_gb, 1),
            "swap_gb": round(swap_gb, 2),
            "swap_total_gb": round(swap_total_gb, 2),
            "wired_gb": round(wired_gb, 1),
            "models": [{
                "name": m.get("name") or m.get("model"),
                "size_gb": round(m.get("size", 0) / 1e9, 1),
                "context": m.get("context_length")
                           or (m.get("details") or {}).get("context_length"),
                "minutes_left": None if (v := minutes_left(m)) is None else round(v),
            } for m in models],
            "burst": None if not burst else {
                "processed": burst.last_processed,
                "total": burst.total,
                "rate_tok_s": round(burst.rate),
                "eta_s": round(burst.eta_seconds),
                "age_s": round(age or 0),
            },
            "cache": None if not view.cache else {
                "total": view.cache[0], "matched": view.cache[1],
            },
            "trouble": [t for _, t in recent_trouble[-5:]],
        }, indent=2))
        return 0

    for m in models:
        left = minutes_left(m)
        expiry = "in use" if left is not None and left <= 0 else (
            f"{left:.0f} min" if left is not None else "?")
        ctx = m.get("context_length") or (m.get("details") or {}).get("context_length") or "?"
        print(f"{m.get('name') or m.get('model'):<28} "
              f"{m.get('size', 0) / 1e9:5.1f} GB  ctx={ctx}  expires {expiry}")
    if not models:
        print("(no model resident)")

    for cpu, rss in live:
        print(f"{'runner':<28} {rss:5.1f} GB  cpu {cpu:.0f}%")
    print(f"{'memory':<28} {avail_gb:5.1f} GB available "
          f"({free_gb:.1f} free), {wired_gb:.0f} GB wired")
    swap_note = ""
    if swap_total_gb:
        swap_note = f" of a {swap_total_gb:.1f} GB file"
    print(f"{'swap':<28} {swap_gb:5.2f} GB used{swap_note}")

    if burst:
        pct = 100.0 * burst.last_processed / burst.total if burst.total else 0
        seen = f"{age:.0f}s ago" if age is not None else "unknown"
        print(f"\nprompt processing            {burst.last_processed:,} of "
              f"{burst.total:,} ({pct:.0f}%), last update {seen}")
        if burst.rate and burst.remaining > DONE_SLACK:
            print(f"{'':29}{burst.rate:.0f} tok/s, "
                  f"{burst.eta_seconds / 60:.1f} min left")
        elif burst.rate:
            print(f"{'':29}finished at {burst.rate:.0f} tok/s")
    if view.cache:
        total, matched = view.cache
        share = 100.0 * matched / total if total else 0
        print(f"last cache hit               {matched:,} of {total:,} reused ({share:.0f}%)")
    for t in view.timings[-2:]:
        print(f"last timing                  {t}")
    for at, text in recent_trouble[-3:]:
        mins = "" if at is None else f"{(now - at).total_seconds() / 60:.0f} min ago: "
        print(f"TROUBLE                      {mins}{text}")

    print(f"\nVERDICT: {verdict}")
    if looping:
        total = f"{burst.total:,}" if burst else "same"
        print(f"LOOPING: the {total}-token prompt restarted {view.restarts} times. "
              "A prompt above the model window cannot keep a prefix cache, so every "
              "turn re-prefills everything. Stop the client, not the model.")
    if prefilling and burst and burst.eta_seconds > 120:
        print("Do not evict now: the work in flight would be repeated from zero.")
    if avail_gb < 4:
        print(f"Memory is nearly gone ({avail_gb:.1f} GB available). Expect swap and "
              "a collapse in throughput.")
    # Available stays reassuring while the machine pages, because it counts reclaimable
    # file cache. Swap is the figure that moves before throughput falls off a cliff:
    # measured 490 tok/s with room to spare against ~157 tok/s once memory was exhausted.
    if swap_total_gb and swap_gb > swap_total_gb * 0.75:
        print(f"Already paging: {swap_gb:.1f} GB of the {swap_total_gb:.1f} GB swap file "
              "is in use. Prefill has been measured at a third of its normal rate in this "
              "state. Nothing to evict if only one model is resident; the KV cache of a "
              "long conversation is what grew.")
    elif swap_gb > 4:
        print(f"Swap is at {swap_gb:.1f} GB and rising is the warning sign, "
              "not 'available'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
