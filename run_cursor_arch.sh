#!/bin/zsh
# Run archbench against Cursor CLI models (ask-mode + native tools over shopapi).
# Usage:
#   ./run_cursor_arch.sh composer-2.5
#   BENCH_TASKS=tenant_invoice_isolation ./run_cursor_arch.sh composer-2.5
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/archbench"
PY="$(command -v python3.14 || true)"
[[ -n "$PY" ]] || PY="$(uv python find 3.14 2>/dev/null || true)"
[[ -n "$PY" ]] || { echo "need python3.14"; exit 1; }

if ! command -v agent >/dev/null 2>&1; then
  echo "Cursor CLI \`agent\` not on PATH. Install: curl https://cursor.com/install -fsS | bash"
  exit 1
fi
agent status >/dev/null || { echo "Not logged in. Run: agent login"; exit 1; }

models=("$@")
if (( ${#models[@]} == 0 )); then
  models=(composer-2.5)
fi

export BENCH_PROVIDER=cursor
export BENCH_OUT="${BENCH_OUT:-$ROOT/results}"

for model in "${models[@]}"; do
  tag="cursor_$(echo "$model" | sed 's/[^a-zA-Z0-9._-]/_/g')_arch"
  echo "==== archbench cursor model=$model tag=$tag ===="
  BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" "$ROOT/archbench/arch_bench.py" \
    || echo "WARN: failed $model"
done
echo "==== cursor archbench done $(date) ===="
