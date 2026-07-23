#!/bin/zsh
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
LOG="$HOME/.ollama/bench/results/pyhard_resume_wrapper.log"
exec >>"$LOG" 2>&1
echo "==== resume start $(date) ===="
"$HOME/.ollama/bench/run_hard_bench_py.sh" \
  'qwen3.6:35b-a3b-coding-bf16' \
  'north-mini-code-1.0:bf16'
# Rebuild full compare from all latest
/Users/deirh/.local/bin/python3.14 <<'PY'
import json
from pathlib import Path
out = Path.home()/".ollama"/"bench"/"results"
# map display name -> glob/tag prefix
specs = [
    ("Qwen3-Coder-Next Q8", "qwen3-coder-next_q8_0_pyhard"),
    ("Qwen3-Coder 30B-A3B FP16", "qwen3-coder_30b-a3b-fp16_pyhard"),
    ("gpt-oss 120B", "gpt-oss_120b_pyhard"),
    ("Qwen3.5 35B-A3B Coding BF16", "qwen3.5_35b-a3b-coding-bf16_pyhard"),
    ("Qwen3.6 35B-A3B Coding BF16", "qwen3.6_35b-a3b-coding-bf16_pyhard"),
    ("North Mini Code 1.0 BF16", "north-mini-code-1.0_bf16_pyhard"),
]
present = {}
for name, tag in specs:
    p = out / f"{tag}_pyhard_latest.json"
    if p.exists():
        present[name] = json.loads(p.read_text())
if not present:
    raise SystemExit("no results")
task_ids = [r["task"] for r in next(iter(present.values()))]
lines = ["# Python 3.14 hard bench @ 64k / 16k (all models)", ""]
lines.append("| Task |" + "".join(f" {k} |" for k in present))
lines.append("|------|" + "------|" * len(present))
for t in task_ids:
    row = f"| {t} |"
    for rs in present.values():
        r = next((x for x in rs if x["task"] == t), None)
        if r:
            row += f" {'PASS' if r['ok'] else 'FAIL'} {r['score']}/{r['max_score']} |"
        else:
            row += " - |"
    lines.append(row)
lines.append("")
for name, rs in present.items():
    score = sum(r["score"] for r in rs)
    mx = sum(r["max_score"] for r in rs)
    wall = round(sum(r["wall_s"] for r in rs), 1)
    tps = [r["toks_per_s"] for r in rs if r.get("toks_per_s")]
    avg = round(sum(tps)/len(tps),1) if tps else 0
    lines.append(f"- **{name}**: {score}/{mx}, wall {wall}s, ~{avg} tok/s, pass {sum(1 for r in rs if r['ok'])}/{len(rs)}")
path = out / "compare_pyhard_64k.md"
path.write_text("\n".join(lines)+"\n")
print("\n".join(lines))
print("Wrote", path)
PY
echo "==== resume done $(date) ===="
