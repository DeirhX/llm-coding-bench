#!/usr/bin/env python3.14
"""Summarize archbench results; if top scores cluster, run claim probe."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bench_lib.paths import results_dir  # noqa: E402

OUT = results_dir("archbench")
ROOT = Path(__file__).resolve().parent
PY = sys.executable

MODELS = [
    "qwen3-coder-next:q8_0",
    "qwen3-coder:30b-a3b-fp16",
    "gpt-oss:120b",
    "qwen2.5-coder:32b-instruct-q8_0",
    "devstral:24b-small-2505-fp16",
    "qwen3.5:35b-a3b-coding-bf16",
    "qwen3.6:35b-a3b-coding-bf16",
    "north-mini-code-1.0:bf16",
    "llama3.3:70b-instruct-q8_0",
    # deepseek-r1 skipped (no tool use on arch protocol)
]


def tag_for(model: str, suffix: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", model) + suffix


def load_arch(model: str) -> dict | None:
    p = OUT / f"{tag_for(model, '_arch')}_latest.json"
    if not p.exists():
        return None
    rows = json.loads(p.read_text())
    if isinstance(rows, dict) and rows.get("skipped"):
        return None
    if not isinstance(rows, list) or not rows:
        return None
    score = sum(int(r.get("score") or 0) for r in rows)
    mx = sum(int(r.get("max_score") or 0) for r in rows)
    passed = sum(1 for r in rows if r.get("ok"))
    tools = sum(int(r.get("tool_calls") or 0) for r in rows)
    wall = sum(float(r.get("wall_s") or 0) for r in rows)
    return {
        "model": model,
        "score": score,
        "max": mx,
        "pass": passed,
        "tasks": len(rows),
        "tool_calls": tools,
        "wall_s": round(wall, 1),
        "rows": rows,
        "path": str(p),
    }


def load_claim(model: str) -> dict | None:
    p = OUT / f"{tag_for(model, '_claim')}_latest.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    return d


def is_tied(board: list[dict], margin: int = 6) -> list[str]:
    """Return models in the top cluster if a meaningful tie exists."""
    complete = [b for b in board if b["tasks"] >= 9]
    if len(complete) < 2:
        return []
    complete.sort(key=lambda b: (-b["score"], b["wall_s"]))
    top = complete[0]["score"]
    cluster = [b for b in complete if top - b["score"] <= margin]
    # tie if 2+ within margin OR 3+ share same score
    same = [b for b in complete if b["score"] == top]
    if len(same) >= 2 or len(cluster) >= 3:
        return [b["model"] for b in cluster]
    # also if top two within margin
    if len(complete) >= 2 and complete[0]["score"] - complete[1]["score"] <= margin:
        return [complete[0]["model"], complete[1]["model"]]
    return []


def run_claim(models: list[str]) -> None:
    for model in models:
        tag = tag_for(model, "_claim")
        latest = OUT / f"{tag}_latest.json"
        if latest.exists():
            print(f"claim skip (exists) {model}")
            continue
        print(f"---- claim probe {model} ----")
        env = os.environ.copy()
        env["BENCH_MODEL"] = model
        env["BENCH_TAG"] = tag
        subprocess.run([PY, "-m", "benches.claim"], cwd=str(_REPO), env=env, check=False)


def write_compare(board: list[dict], tied: list[str], claims: dict[str, dict]) -> Path:
    lines = [
        "# Archbench results (tools-first)",
        "",
        "Fixture: shopapi · 9 tasks / 90 pts · tool budget 30/task",
        "",
        "| Model | Score | Pass | Tool calls | Wall s |",
        "|---|---:|---:|---:|---:|",
    ]
    for b in sorted(board, key=lambda x: (-x["score"], x["wall_s"])):
        lines.append(
            f"| {b['model']} | {b['score']}/{b['max']} | {b['pass']}/{b['tasks']} | {b['tool_calls']} | {b['wall_s']} |"
        )
    lines.append("")
    if tied:
        lines.append(f"Tie cluster (claim probe): {', '.join(tied)}")
        lines.append("")
    if claims:
        lines += [
            "## Claim probe (20 T/F + evidence)",
            "",
            "| Model | Score | Correct | Wrong | Missing | Wall s |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for m, c in sorted(claims.items(), key=lambda kv: (-(kv[1].get("score") or 0), kv[0])):
            lines.append(
                f"| {m} | {c.get('score')}/{c.get('max_score')} | {c.get('correct')} | {c.get('wrong')} | {c.get('missing')} | {c.get('wall_s')} |"
            )
        lines.append("")

    # per-task matrix
    if board:
        task_ids = [r["task"] for r in board[0]["rows"]]
        # union of tasks
        task_ids = []
        seen = set()
        for b in board:
            for r in b["rows"]:
                if r["task"] not in seen:
                    seen.add(r["task"])
                    task_ids.append(r["task"])
        lines += ["## Per-task scores", ""]
        header = "| Task | " + " | ".join(b["model"].split(":")[0][:18] for b in sorted(board, key=lambda x: -x["score"])) + " |"
        lines.append(header)
        lines.append("|---|" + "|".join(["---:" for _ in board]) + "|")
        ordered = sorted(board, key=lambda x: -x["score"])
        for tid in task_ids:
            cells = []
            for b in ordered:
                row = next((r for r in b["rows"] if r["task"] == tid), None)
                cells.append(f"{row['score']}/{row['max_score']}" if row else "—")
            lines.append(f"| {tid} | " + " | ".join(cells) + " |")
        lines.append("")

    # judgment
    lines += ["## Judgment", ""]
    lines.append(_judgment(board, claims, tied))
    path = OUT / "compare_archbench.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print("Wrote", path)
    return path


def _judgment(board: list[dict], claims: dict[str, dict], tied: list[str]) -> str:
    complete = [b for b in board if b["tasks"] >= 9]
    if not complete:
        return "No complete archbench runs yet."
    complete.sort(key=lambda b: (-b["score"], b["wall_s"]))
    top = complete[0]
    parts = [
        f"Archbench leader: **{top['model']}** at {top['score']}/{top['max']} "
        f"({top['pass']}/{top['tasks']} pass) in {top['wall_s']}s with {top['tool_calls']} tool calls.",
    ]
    # efficiency among top-3 scores
    elite = [b for b in complete if b["score"] >= top["score"] - 10]
    if len(elite) >= 2:
        eff = sorted(elite, key=lambda b: (b["wall_s"], b["tool_calls"]))
        parts.append(
            f"Among near-top scorers, fastest is **{eff[0]['model']}** "
            f"({eff[0]['wall_s']}s, {eff[0]['tool_calls']} tools)."
        )
    # tool use failures
    no_tools = [b for b in complete if b["tool_calls"] == 0]
    if no_tools:
        parts.append(
            "Models with zero tool calls (protocol failure / refused to explore): "
            + ", ".join(b["model"] for b in no_tools)
            + "."
        )
    if claims:
        crow = sorted(claims.values(), key=lambda c: (-(c.get("score") or 0), c.get("wall_s") or 0))
        best = crow[0]
        n_claims = int(best.get("max_score") or best.get("total") or 20)
        parts.append(
            f"Claim probe leader: **{best.get('model')}** "
            f"{best.get('correct')}/{n_claims} correct (score {best.get('score')}/{best.get('max_score')})."
        )
        # where they disagree on hard negatives
        hard = ["c03", "c04", "c07", "c09", "c11", "c13", "c15", "c16", "c17", "c19"]
        # summarize average correct
        avg = sum(c.get("correct") or 0 for c in claims.values()) / max(1, len(claims))
        parts.append(
            f"Claim probe mean correct: {avg:.1f}/{n_claims} — this is the discriminative ruler when arch scores cluster."
        )
    elif tied:
        parts.append(
            f"Arch scores clustered ({', '.join(tied)}); claim probe was indicated but produced no results."
        )
    else:
        parts.append("Arch scores separated enough that claim probe was not required for ranking.")

    # skeptical closer
    spread = complete[0]["score"] - complete[-1]["score"]
    parts.append(
        f"Score spread top−bottom: {spread} pts on /{complete[0]['max']}. "
        + (
            "Useful separation."
            if spread >= 15
            else "Weak separation on archbench alone — prefer claim probe / harder traps."
        )
    )
    return " ".join(parts)


def main() -> int:
    board = []
    for m in MODELS:
        row = load_arch(m)
        if row:
            board.append(row)
    if not board:
        print("No archbench results found", file=sys.stderr)
        return 1

    tied = is_tied(board)
    print("Tie cluster:", tied or "(none)")
    if tied:
        # also run claim on full board if almost everyone tied
        targets = tied
        if len(tied) >= 3:
            targets = [b["model"] for b in board if b["tasks"] >= 9]
        run_claim(targets)

    claims = {}
    for m in MODELS:
        c = load_claim(m)
        if c:
            claims[m] = c

    # if still tied on claims among same cluster, note it (no third bench auto — judgment explains)
    write_compare(board, tied, claims)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
