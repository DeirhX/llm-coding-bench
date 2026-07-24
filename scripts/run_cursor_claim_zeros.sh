#!/bin/zsh
# Force re-run Cursor claim cells that "completed" with 0/20 answers
# (gap queue treats n_per_claim>=20 as done even when all missing).
set -uo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"
LOG="$ROOT/results/cursor_claim_zeros.log"
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== cursor claim zeros start $(date) ===="
agent status >/dev/null || { echo "not logged in to agent"; exit 1; }

export BENCH_PROVIDER=cursor
export BENCH_CURSOR_MODE="${BENCH_CURSOR_MODE:-ask}"
export BENCH_TASK_TIMEOUT_S="${BENCH_TASK_TIMEOUT_S:-600}"
export BENCH_CURSOR_TIMEOUT="${BENCH_CURSOR_TIMEOUT:-$BENCH_TASK_TIMEOUT_S}"
export BENCH_OUT="$ROOT/results"
export BENCH_MERGE_LATEST=0

# Archive the zeroed latest so we don't confuse ranking mid-run.
archive_zero() {
  local path="$1"
  [[ -f "$path" ]] || return 0
  local bak="${path%.json}_zero_$(/bin/date +%Y%m%d_%H%M%S).json"
  /bin/cp "$path" "$bak"
  echo "archived $path -> $bak"
}

MODELS=(
  'composer-2.5'
  'claude-4.5-haiku'
)

for model in "${MODELS[@]}"; do
  safe="$(echo "$model" | sed 's/[^a-zA-Z0-9._-]/_/g')"
  tag="cursor_${safe}_claim"
  latest="$ROOT/results/archbench/${tag}_latest.json"
  echo "==== claim rerun model=$model $(date) ===="
  if [[ -f "$latest" ]]; then
    "$PY" - <<PY
import json
from pathlib import Path
p = Path("$latest")
o = json.loads(p.read_text())
print(f"before score={o.get('score')}/{o.get('max_score')} correct={o.get('correct')} missing={o.get('missing')}")
PY
  fi
  archive_zero "$latest"
  export BENCH_MODEL="$model"
  export BENCH_TAG="$tag"
  "$PY" -u "$ROOT/run.py" run claim || echo "WARN claim rc=$?"
  if [[ -f "$latest" ]]; then
    "$PY" - <<PY
import json
from pathlib import Path
o = json.loads(Path("$latest").read_text())
s, c = int(o.get("score") or 0), int(o.get("correct") or 0)
print(f"after score={o.get('score')}/{o.get('max_score')} correct={o.get('correct')}")
raise SystemExit(0 if c > 0 else 1)
PY
    if [[ $? -eq 0 ]]; then
      echo "---- ok $model ----"
    else
      echo "---- WARN still zero $model ----"
    fi
  fi
done

"$PY" -u "$ROOT/run.py" report claim --no-color || true
echo "==== cursor claim zeros ALL DONE $(date) ===="
