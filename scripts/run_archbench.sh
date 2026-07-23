#!/bin/zsh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  PY="$(command -v python3.14 || true)"
fi
if [[ -z "$PY" ]]; then
  echo "Need python3.14" >&2
  exit 1
fi

if [[ "${1:-}" == "selftest" ]]; then
  exec "$PY" "$ROOT/run.py" selftest arch
fi

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 selftest | MODEL [MODEL...]" >&2
  exit 2
fi

export BENCH_OUT="${BENCH_OUT:-$ROOT/results}"
for MODEL in "$@"; do
  TAG="$(echo "$MODEL" | sed 's/[^a-zA-Z0-9._-]/_/g')_arch"
  echo "==== $MODEL tag=$TAG ===="
  BENCH_MODEL="$MODEL" BENCH_TAG="$TAG" "$PY" "$ROOT/run.py" run arch
done
