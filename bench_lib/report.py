"""Unified result reporting — terminal tables + markdown leaderboards."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bench_lib.paths import REPO_ROOT, results_dir
from benches.registry import BENCHES, BenchSpec, normalize_bench_id

# ANSI (disabled when not a TTY — callers can force via ``use_color``).
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_CYAN = "\033[36m"

_BENCH_TITLES = {spec.id: spec.title for spec in BENCHES.values()}


@dataclass
class RunSummary:
    bench: str
    model: str
    tag: str
    score: int
    max_score: int
    passed: int
    tasks: int
    wall_s: float
    path: Path
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def pct(self) -> float:
        return (100.0 * self.score / self.max_score) if self.max_score else 0.0


def _safe_json(path: Path) -> Any | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        import warnings
        warnings.warn(f"report: skipped malformed file {path}: {e}", UserWarning, stacklevel=2)
        return None
    if data is None:
        # Valid JSON but null — treat as malformed
        warnings.warn(f"report: skipped null data in {path}", UserWarning, stacklevel=2)
    return data


def _tag_from_latest(name: str, suffix: str) -> str:
    if name.endswith(suffix):
        return name[: -len(suffix)]
    return Path(name).stem


def summarize_task_list(
    bench: str,
    path: Path,
    data: list[dict[str, Any]],
    *,
    suffix: str = "_latest.json",
) -> RunSummary | None:
    if not isinstance(data, list):
        return None
    if not data:
        return None
    model = str(data[0].get("model") or "?")
    score = sum(int(row.get("score") or 0) for row in data)
    mx = sum(int(row.get("max_score") or 0) for row in data)
    passed = sum(1 for row in data if row.get("ok"))
    wall = sum(float(row.get("wall_s") or 0) for row in data)
    tools = sum(
        int(row.get("tool_calls") or 0)
        for row in data
        if row.get("tool_calls") is not None
    )
    return RunSummary(
        bench=bench,
        model=model,
        tag=_tag_from_latest(path.name, suffix),
        score=score,
        max_score=mx,
        passed=passed,
        tasks=len(data),
        wall_s=round(wall, 1),
        path=path,
        extra={"tool_calls": tools} if tools else {},
    )


def summarize_claim(path: Path, data: dict[str, Any]) -> RunSummary | None:
    if not isinstance(data, dict):
        return None
    looks_like_claim = (
        data.get("bench") == "claim"
        or "per_claim" in data
        or ("correct" in data and "wrong" in data and "max_score" in data)
     )
    if not looks_like_claim:
        return None
    n_claims = len(data.get("per_claim") or []) or 15
    return RunSummary(
        bench="claim",
        model=str(data.get("model") or "?"),
        tag=_tag_from_latest(path.name, "_latest.json"),
        score=int(data.get("score") or 0),
        max_score=int(data.get("max_score") or n_claims),
        passed=int(data.get("correct") or 0),
        tasks=n_claims,
        wall_s=float(data.get("wall_s") or 0),
        path=path,
        extra={
            "wrong": data.get("wrong"),
            "missing": data.get("missing"),
        },
    )


def _accept_latest(spec: BenchSpec, path: Path) -> bool:
    name = path.name
    if spec.id == "arch":
        if not name.endswith("_latest.json") or name.endswith("_pyhard_latest.json"):
            return False
        if "_claim_" in name or name.endswith("_claim_latest.json"):
            return False
        if "_summary" in name:
            return False
        return "_arch" in name or name.endswith("_arch_latest.json")
    if spec.id == "claim":
        return name.endswith("_latest.json") and (
            "_claim_" in name or name.endswith("_claim_latest.json")
        )
    if spec.id == "repohard":
        if "_summary" in name:
            return False
        return name.endswith("_latest.json") and "repohard" in name
    if spec.id == "audittrap":
        if "_summary" in name:
            return False
        return name.endswith("_latest.json") and "audittrap" in name
    return True


def _summarize_file(spec: BenchSpec, path: Path) -> RunSummary | None:
    data = _safe_json(path)
    if data is None:
        return None
    if spec.id == "claim":
        if not isinstance(data, dict):
            import warnings
            warnings.warn(f"report: expected dict for claim bench, got {type(data).__name__} in {path}", UserWarning, stacklevel=2)
            return None
        return summarize_claim(path, data)
    if isinstance(data, list):
        return summarize_task_list(spec.id, path, data, suffix=spec.latest_suffix)
    import warnings
    warnings.warn(f"report: unexpected data type {type(data).__name__} in {path}", UserWarning, stacklevel=2)
    return None


def _is_noise_tag(tag: str) -> bool:
    lowered = tag.lower()
    return any(token in lowered for token in ("smoke", "_think", "selftest"))


def _prefer_key(run: RunSummary) -> tuple:
    """Higher is better when picking one run per (bench, model)."""
    expected = BENCHES[run.bench].expected_tasks if run.bench in BENCHES else 1
    complete = 1 if run.tasks >= expected else 0
    clean = 0 if _is_noise_tag(run.tag) else 1
    return (complete, clean, run.score, -run.wall_s)


def _dedupe_best(runs: list[RunSummary]) -> list[RunSummary]:
    best: dict[tuple[str, str], RunSummary] = {}
    for run in runs:
        key = (run.bench, run.model)
        current = best.get(key)
        if current is None or _prefer_key(run) > _prefer_key(current):
            best[key] = run
    return list(best.values())


def collect_runs(bench_filter: str | None = None, *, dedupe: bool = True) -> list[RunSummary]:
    """Scan ``results/`` for latest run files and summarize them."""
    want = normalize_bench_id(bench_filter) if bench_filter else None
    runs: list[RunSummary] = []

    for spec in BENCHES.values():
        if want and spec.id != want:
            continue
        root = results_dir(spec.results_subdir)
        for path in sorted(root.glob(f"*{spec.latest_suffix}")):
            if not _accept_latest(spec, path):
                continue
            summary = _summarize_file(spec, path)
            if summary:
                runs.append(summary)

    if dedupe:
        runs = _dedupe_best(runs)
    runs.sort(key=lambda run: (run.bench, -run.score, run.wall_s, run.model))
    return runs


def _group_by_bench(runs: list[RunSummary]) -> dict[str, list[RunSummary]]:
    grouped: dict[str, list[RunSummary]] = defaultdict(list)
    for run in runs:
        grouped[run.bench].append(run)
    return grouped


def _models_in_multiple_benches(runs: list[RunSummary]) -> dict[str, dict[str, RunSummary]]:
    by_model: dict[str, dict[str, RunSummary]] = defaultdict(dict)
    for run in runs:
        by_model[run.model][run.bench] = run
    return {model: benches for model, benches in by_model.items() if len(benches) >= 2}


def _bar(pct: float, width: int = 12) -> str:
    filled = max(0, min(width, int(round(pct / 100 * width))))
    return "█" * filled + "░" * (width - filled)


def _colorize(text: str, code: str, *, use_color: bool) -> str:
    if not use_color:
        return text
    return f"{code}{text}{_RESET}"


def _score_color(pct: float) -> str:
    if pct >= 80:
        return _GREEN
    if pct >= 50:
        return _YELLOW
    return _RED


def format_terminal(runs: list[RunSummary], *, use_color: bool = True) -> str:
    if not runs:
        return "No latest results found under results/."

    lines: list[str] = []
    for bench, group in _group_by_bench(runs).items():
        title = _BENCH_TITLES.get(bench, bench)
        lines.append(_colorize(f"══ {title} ══", _BOLD + _CYAN, use_color=use_color))
        header = f"{'Model':<42} {'Score':>10} {'Pass':>8} {'Wall':>8}  {'':12}  Tag"
        lines.append(_colorize(header, _DIM, use_color=use_color))
        for run in group:
            score = f"{run.score}/{run.max_score}"
            pas = f"{run.passed}/{run.tasks}"
            wall = f"{run.wall_s:.0f}s"
            bar = _bar(run.pct)
            score_s = _colorize(f"{score:>10}", _score_color(run.pct), use_color=use_color)
            lines.append(
                f"{run.model[:42]:<42} {score_s} {pas:>8} {wall:>8}  {bar}  {run.tag}"
            )
        lines.append("")

    multi = _models_in_multiple_benches(runs)
    if multi:
        lines.append(_colorize("══ Cross-bench ══", _BOLD + _CYAN, use_color=use_color))
        benches = sorted({bench for group in multi.values() for bench in group})
        hdr = f"{'Model':<36}" + "".join(f" {bench:>12}" for bench in benches)
        lines.append(_colorize(hdr, _DIM, use_color=use_color))
        for model, group in sorted(multi.items(), key=lambda kv: -sum(x.score for x in kv[1].values())):
            cells = [
                f"{group[bench].score}/{group[bench].max_score}" if bench in group else "—"
                for bench in benches
            ]
            lines.append(f"{model[:36]:<36}" + "".join(f" {cell:>12}" for cell in cells))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def format_markdown(runs: list[RunSummary]) -> str:
    if not runs:
        return "# Bench report\n\nNo latest results found.\n"

    results_path = results_dir()
    if results_path.is_relative_to(REPO_ROOT):
        source = results_path.relative_to(REPO_ROOT)
    else:
        source = results_path

    lines = [
        "# Bench report",
        "",
        f"Source: `{source}`",
        "",
        "> **Harness eras:** Cursor gap-queue runs after 2026-07-23 17:19 CEST are "
        "**post-harness** (temp repohard workspace, full patches, claim c01–c20, "
        "arch `required_files` from assignment). Older `*_latest.json` may be "
        "**pre-harness** — see `results/POST_HARNESS.md`. Do not mix eras in one cell.",
        "",
    ]
    for bench, group in _group_by_bench(runs).items():
        lines += [
            f"## {_BENCH_TITLES.get(bench, bench)}",
            "",
            "| Model | Score | % | Pass | Wall s | Tag |",
            "|---|---:|---:|---:|---:|---|",
        ]
        for run in group:
            lines.append(
                f"| {run.model} | {run.score}/{run.max_score} | {run.pct:.0f}% | "
                f"{run.passed}/{run.tasks} | {run.wall_s} | `{run.tag}` |"
            )
        lines.append("")

    multi = _models_in_multiple_benches(runs)
    if multi:
        benches = sorted({bench for group in multi.values() for bench in group})
        lines += [
            "## Cross-bench",
            "",
            "| Model | " + " | ".join(benches) + " |",
            "|---|" + "|".join(["---:" for _ in benches]) + "|",
        ]
        for model, group in sorted(multi.items(), key=lambda kv: -sum(x.score for x in kv[1].values())):
            cells = [
                f"{group[bench].score}/{group[bench].max_score}" if bench in group else "—"
                for bench in benches
            ]
            lines.append(f"| {model} | " + " | ".join(cells) + " |")
        lines.append("")

    return "\n".join(lines)


def write_report(
    bench_filter: str | None = None,
    *,
    out_path: Path | None = None,
    use_color: bool = True,
    dedupe: bool = True,
) -> tuple[str, Path]:
    runs = collect_runs(bench_filter, dedupe=dedupe)
    terminal = format_terminal(runs, use_color=use_color)
    md = format_markdown(runs)
    dest = out_path or (results_dir() / "REPORT.md")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(md, encoding="utf-8")
    return terminal, dest


def model_slug(model: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", model)
