#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path

def walk_scores(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    d = json.loads(path.read_text())
    by = {}
    def walk(x):
        if isinstance(x, dict):
            if "score" in x and "task" in x:
                by[x["task"]] = x
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(d)
    if by:
        s = sum(int(t.get("score") or 0) for t in by.values())
        m = sum(int(t.get("max_score") or 0) for t in by.values())
        return s, m or 0
    if isinstance(d, list):
        s = sum(int(t.get("score") or 0) for t in d if isinstance(t, dict))
        m = sum(int(t.get("max_score") or 0) for t in d if isinstance(t, dict))
        return s, m
    if isinstance(d, dict) and "score" in d:
        return int(d["score"]), int(d.get("max_score") or 0)
    return 0, 0

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--arch", required=True)
    ap.add_argument("--pyhard", required=True)
    ap.add_argument("--repo-max", type=int, required=True)
    ap.add_argument("--arch-max", type=int, required=True)
    ap.add_argument("--py-max", type=int, required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    rs, rm = walk_scores(Path(args.repo))
    as_, am = walk_scores(Path(args.arch))
    ps, pm = walk_scores(Path(args.pyhard))
    # Prefer declared probe maxima when file max incomplete
    rm = args.repo_max if rm < args.repo_max else rm
    am = args.arch_max if am < args.arch_max else am
    pm = args.py_max if pm < args.py_max else pm
    rp, apct, pp = (100 * rs / rm if rm else 0), (100 * as_ / am if am else 0), (100 * ps / pm if pm else 0)
    mean = (rp + apct + pp) / 3
    worst = min(rp, apct, pp)
    row = {
        "variant": args.variant,
        "repo": f"{rs}/{rm}",
        "arch": f"{as_}/{am}",
        "pyhard": f"{ps}/{pm}",
        "repo_pct": round(rp, 1),
        "arch_pct": round(apct, 1),
        "py_pct": round(pp, 1),
        "universal_mean": round(mean, 1),
        "universal_min": round(worst, 1),
    }
    out = Path(args.out)
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    print("SCORE", json.dumps(row))

if __name__ == "__main__":
    main()
