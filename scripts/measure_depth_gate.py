#!/usr/bin/env python3
"""Compare the baseline and contract arms of a depth-gate measurement run.

Reports each bench's score both ways and, for the answers themselves, how many claims the verifier
can confirm. A contract that raised verified claims while dropping arch points would be a bad trade
and this is where that shows up; so would one that changed nothing at all, which is the likelier
outcome and the one worth being able to state plainly.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cc_ledger  # noqa: E402
import cc_verify  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def summarise(path: str) -> dict:
    """Rows come from the per-task file; the score comes from the summary beside it.

    They are separate files and only the summary carries the graded total, which the first version
    of this script missed -- it printed `None/None` for every arm and would have been read as "the
    contract changed nothing" by anyone who did not look twice.
    """
    try:
        data = json.load(open(path))
    except Exception:
        return {}
    rows = data if isinstance(data, list) else (data.get("rows") or data.get("results") or [])
    rows = [r for r in rows if isinstance(r, dict)]
    meta = {} if isinstance(data, list) else dict(data)
    if meta.get("score") is None:
        summary = re.sub(r"_[a-z]+_\d{8}_\d{6}\.json$", "_summary.json", path)
        try:
            meta.update(json.load(open(summary)))
        except Exception:
            pass
    per_task = [(r.get("task"), r.get("score"), r.get("max_score")) for r in rows]
    # Citations in a bench answer point into the bench's own fixture tree, which is a temporary
    # copy that no longer exists by the time this runs -- not into this repository. Checking them
    # against the repo root reported a correct citation as unverified, which is precisely the
    # mislabelling the verifier was built to avoid, committed by the script measuring it. So a
    # citation whose file is not present is counted as unresolvable and kept out of the verdict.
    verified = unverified = unresolvable = claims = 0
    for row in rows:
        text = str(row.get("raw_content") or row.get("content") or "")
        for claim in cc_ledger.claims_from_text(text)[0]:
            claims += 1
            for ev in claim.evidence:
                if ev.kind != cc_ledger.FILE_QUOTE or not (ev.path and ev.start and ev.quote):
                    continue
                if not (REPO / ev.path).is_file():
                    unresolvable += 1
                    continue
                v = cc_verify.file_quote(str(REPO), ev.path, ev.start, ev.end or ev.start, ev.quote)
                verified += 1 if v.ok else 0
                unverified += 0 if v.ok else 1
    return {
        "score": meta.get("score"), "max": meta.get("max_score"), "tasks": len(rows),
        "per_task": per_task,
        "wall_min": round(sum(float(r.get("wall_s") or 0) for r in rows) / 60, 1),
        "claims": claims, "verified": verified, "unverified": unverified,
        "unresolvable": unresolvable,
    }


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    print("%-12s %-9s %-9s %-6s %-8s %s"
          % ("bench", "arm", "score", "tasks", "wall", "claim blocks (ok/bad/outside repo)"))
    rows = {}
    for bench in ("arch", "claim", "audittrap"):
        for arm in ("baseline", "contract"):
            files = sorted(glob.glob(str(out / "**" / ("*depth_%s_%s*.json" % (bench, arm))),
                                     recursive=True))
            files = [f for f in files if "summary" not in f and "latest" not in f]
            if not files:
                print("%-12s %-9s %s" % (bench, arm, "(no result)"))
                continue
            s = summarise(files[-1])
            rows[(bench, arm)] = s
            print("%-12s %-9s %-9s %-6s %-8s %d (%d/%d/%d)"
                  % (bench, arm, "%s/%s" % (s.get("score"), s.get("max")), s.get("tasks"),
                     "%.1fm" % s.get("wall_min", 0), s["claims"], s["verified"],
                     s["unverified"], s["unresolvable"]))

    print()
    for bench in ("arch", "claim", "audittrap"):
        a, b = rows.get((bench, "baseline")), rows.get((bench, "contract"))
        if not (a and b) or a.get("score") is None or b.get("score") is None:
            continue
        moved = [t for t, x, y in zip([p[0] for p in a["per_task"]],
                                      [p[1] for p in a["per_task"]],
                                      [p[1] for p in b["per_task"]]) if x != y]
        delta = b["score"] - a["score"]
        verdict = "no change" if delta == 0 else ("+%d" % delta if delta > 0 else str(delta))
        print("%-12s contract %s (%s -> %s of %s)%s"
              % (bench, verdict, a["score"], b["score"], a["max"],
                 ", tasks that moved: %s" % ", ".join(moved) if moved else ""))
    json.dump(rows and {"%s/%s" % k: v for k, v in rows.items()},
              open(out / "comparison.json", "w"), indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
