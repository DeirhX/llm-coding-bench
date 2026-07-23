#!/bin/zsh
# List Cursor CLI models available to the logged-in account.
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PY="$(command -v python3.14 || true)"
[[ -n "$PY" ]] || PY="$(uv python find 3.14 2>/dev/null || true)"
exec "$PY" -c '
from bench_lib.cursor_cli import list_models
for mid, name in list_models():
    print(f"{mid}\t{name}")
'
