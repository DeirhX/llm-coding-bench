#!/bin/zsh
# Run Python-3.14 hard bench for one or more models.
# Usage:
#   ~/.ollama/bench/run_hard_bench_py.sh
#   ~/.ollama/bench/run_hard_bench_py.sh 'qwen3-coder-next:q8_0' 'gpt-oss:120b'
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"

PY="${BENCH_PYTHON:-}"
if [[ -z "$PY" ]]; then
  if command -v uv >/dev/null 2>&1; then
    PY="$(uv python find 3.14)"
  else
    PY="$(command -v python3.14 || true)"
  fi
fi
if [[ -z "$PY" || ! -x "$PY" ]]; then
  echo "Need Python 3.14 (install via: uv python install 3.14)" >&2
  exit 1
fi

models=("$@")
if (( $# == 0 )); then
  models=(
    'qwen3-coder-next:q8_0'
    'qwen3-coder:30b-a3b-fp16'
    'gpt-oss:120b'
    'qwen3.5:35b-a3b-coding-bf16'
  )
fi

OUT="$HOME/.ollama/bench/results"
mkdir -p "$OUT"
LOG="$OUT/hard_bench_py_runner.log"
exec > >(tee -a "$LOG") 2>&1

echo "==== pyhard start $(date) ===="
echo "python=$PY"
"$PY" -c 'import sys; print(sys.version)'

for model in "${models[@]}"; do
  tag="$(echo "$model" | sed 's/[^a-zA-Z0-9._-]/_/g')_pyhard"
  echo "---- $model (tag=$tag) ----"
  BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" "$HOME/.ollama/bench/hard_bench_py.py"
done

"$PY" <<'PY'
import json
from pathlib import Path
out = Path.home() / ".ollama" / "bench" / "results"
tags = {
    "Qwen3-Coder-Next Q8": "qwen3-coder-next_q8_0_pyhard",
    "Qwen3-Coder 30B-A3B FP16": "qwen3-coder_30b-a3b-fp16_pyhard",
    "gpt-oss 120B": "gpt-oss_120b_pyhard",
    "Qwen3.5 35B-A3B Coding BF16": "qwen3.5_35b-a3b-coding-bf16_pyhard",
}
present = {}
for name, tag in tags.items():
    p = out / f"{tag}_pyhard_latest.json"
    if p.exists():
        present[name] = json.loads(p.read_text())
if not present:
    raise SystemExit("no pyhard results yet")
tasks = present[next(iter(present))]
task_ids = [r["task"] for r in tasks]
lines = ["# Python 3.14 hard bench @ 64k ctx / 16k predict", ""]
lines.append("| Task |" + "".join(f" {k} |" for k in present))
lines.append("|------|" + "------|" * len(present))
for t in task_ids:
    row = f"| {t} |"
    for rs in present.values():
        r = next((x for x in rs if x["task"] == t), None)
        if r:
            row += f" {'PASS' if r['ok'] else 'FAIL'} {r['score']}/{r['max_score']} ({r['wall_s']}s, {r['toks_per_s']} t/s) |"
        else:
            row += " - |"
    lines.append(row)
lines.append("")
for name, rs in present.items():
    score = sum(r["score"] for r in rs)
    mx = sum(r["max_score"] for r in rs)
    wall = round(sum(r["wall_s"] for r in rs), 1)
    tps = [r["toks_per_s"] for r in rs if r.get("toks_per_s")]
    avg = round(sum(tps) / len(tps), 1) if tps else 0
    lines.append(f"- **{name}**: {score}/{mx}, wall {wall}s, ~{avg} tok/s, tasks {sum(1 for r in rs if r['ok'])}/{len(rs)}")
path = out / "compare_pyhard_64k.md"
path.write_text("\n".join(lines) + "\n")
print("\n".join(lines))
print(f"Wrote {path}")
PY

echo "==== pyhard done $(date) ===="
