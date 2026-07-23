#!/bin/zsh
set -euo pipefail
ROOT="${0:A:h}"
cd "$ROOT"
PY="${PYTHON:-$HOME/.local/bin/python3.14}"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3.14 || true)"
fi
if [[ -z "${PY}" ]]; then
  echo "Need python3.14" >&2
  exit 1
fi

if [[ "${1:-}" == "selftest" ]]; then
  BENCH_SELFTEST=1 exec "$PY" "$ROOT/arch_bench.py"
fi

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 selftest | MODEL [MODEL...]" >&2
  exit 2
fi

for MODEL in "$@"; do
  TAG="$(echo "$MODEL" | sed 's/[^a-zA-Z0-9._-]/_/g')_arch"
  echo "==== $MODEL tag=$TAG ===="
  BENCH_MODEL="$MODEL" BENCH_TAG="$TAG" "$PY" "$ROOT/arch_bench.py"
done
