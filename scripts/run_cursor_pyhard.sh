#!/bin/zsh
# Run pyhard against one or more Cursor CLI models.
# Usage:
#   ./scripts/run_cursor_pyhard.sh composer-2.5
#   ./scripts/run_cursor_pyhard.sh composer-2.5 gpt-5.4-mini-medium
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
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
  tag="cursor_$(echo "$model" | sed 's/[^a-zA-Z0-9._-]/_/g')_pyhard"
  echo "==== pyhard cursor model=$model tag=$tag ===="
  BENCH_MODEL="$model" BENCH_TAG="$tag" "$PY" "$ROOT/run.py" run pyhard \
    || echo "WARN: failed $model"
done
echo "==== cursor pyhard done $(date) ===="
