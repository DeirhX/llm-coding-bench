#!/bin/zsh
# Sequential Cursor gap-fill on the post-harness tree.
# Runs only missing / stale / null cells — never a blind full suite.
#
# Usage:
#   ./scripts/run_cursor_gap_queue.sh
#   ./scripts/run_cursor_gap_queue.sh --from 'gpt-5.6-sol-high'
#
# Jobs are (model, comma-separated benches). Order matches the remote-gap plan:
#   midtier repair → frontier gaps → claim refresh → repohard leftovers
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${BENCH_PYTHON:-$ROOT/.venv/bin/python}"
[[ -x "$PY" ]] || PY="$(command -v python3.14)"
[[ -n "$PY" ]] || { echo "need python3.14"; exit 1; }

export BENCH_OUT="${BENCH_OUT:-$ROOT/results}"
export BENCH_PROVIDER=cursor
export BENCH_CURSOR_MODE="${BENCH_CURSOR_MODE:-ask}"
# Per-task hard ceiling: skip to next task after 10 minutes.
export BENCH_TASK_TIMEOUT_S="${BENCH_TASK_TIMEOUT_S:-600}"
export BENCH_CURSOR_TIMEOUT="${BENCH_CURSOR_TIMEOUT:-$BENCH_TASK_TIMEOUT_S}"
mkdir -p "$BENCH_OUT" "$BENCH_OUT/archbench" "$BENCH_OUT/repohard"

LOG="${BENCH_QUEUE_LOG:-$BENCH_OUT/cursor_gap_queue.log}"
: >>"$LOG"
exec >>"$LOG" 2>&1

FROM=""
if [[ "${1:-}" == "--from" ]]; then
  FROM="${2:-}"
  shift 2 || true
fi

agent status >/dev/null || { echo "not logged in"; exit 1; }
caffeinate -dims -w $$ &
trap 'kill %1 2>/dev/null || true' EXIT

# model:bench,bench,...
JOBS=(
  # Midtier repair
  'gemini-3.6-flash-high:pyhard,arch,claim,repohard'
  'cursor-grok-4.5-medium:pyhard,arch,claim,repohard'
  'gpt-5.4-mini-high:pyhard,claim,repohard'
  # Frontier / repohard-only models missing pyhard→arch→claim
  'gpt-5.6-sol-high:pyhard,arch,claim'
  'gpt-5.6-terra-high:pyhard,arch,claim'
  'gpt-5.6-luna-high:pyhard,arch,claim'
  'claude-4.5-haiku:pyhard,arch,claim'
  'claude-opus-4-8-thinking-high:pyhard,arch,claim'
  'cursor-grok-4.5-high:pyhard,arch,claim'
  # Claim refresh (15 → 20)
  'composer-2.5:claim'
  'claude-sonnet-5-high:claim'
  'claude-sonnet-5-thinking-high:claim'
  # Repohard leftovers
  'gpt-5.3-codex:repohard'
  'claude-sonnet-5-thinking-high:repohard'
)

safe_name() {
  echo "$1" | sed 's/[^a-zA-Z0-9._-]/_/g'
}

check_complete() {
  local model="$1" bench="$2"
  local safe
  safe="$(safe_name "$model")"
  case "$bench" in
    pyhard)
      "$PY" - <<PY
import json
from pathlib import Path
p = Path("$BENCH_OUT") / f"cursor_${safe}_pyhard_pyhard_latest.json"
alts = list(Path("$BENCH_OUT").glob(f"cursor_${safe}_pyhard*latest.json"))
alts = [x for x in alts if "rescored" not in x.name]
path = p if p.is_file() else (alts[0] if alts else None)
if path is None:
    print("MISSING")
    raise SystemExit(0)
rows = json.loads(path.read_text())
n = len(rows) if isinstance(rows, list) else 0
s = sum(int(r.get("score") or 0) for r in rows) if n else 0
m = sum(int(r.get("max_score") or 0) for r in rows) if n else 0
print(f"{path.name}: n={n} score={s}/{m}")
if n < 9:
    raise SystemExit(1)
PY
      ;;
    arch)
      "$PY" - <<PY
import json
from pathlib import Path
p = Path("$BENCH_OUT/archbench") / f"cursor_${safe}_arch_latest.json"
if not p.is_file():
    print("MISSING")
    raise SystemExit(1)
rows = json.loads(p.read_text())
n = len(rows) if isinstance(rows, list) else 0
s = sum(int(r.get("score") or 0) for r in rows) if n else 0
m = sum(int(r.get("max_score") or 0) for r in rows) if n else 0
print(f"{p.name}: n={n} score={s}/{m}")
if n < 9:
    raise SystemExit(1)
PY
      ;;
    claim)
      "$PY" - <<PY
import json
from pathlib import Path
p = Path("$BENCH_OUT/archbench") / f"cursor_${safe}_claim_latest.json"
if not p.is_file():
    print("MISSING")
    raise SystemExit(1)
o = json.loads(p.read_text())
n = len(o.get("per_claim") or [])
print(f"{p.name}: score={o.get('score')}/{o.get('max_score')} correct={o.get('correct')} n_per={n}")
if n < 20:
    raise SystemExit(1)
PY
      ;;
    repohard)
      "$PY" - <<PY
import json
from pathlib import Path
p = Path("$BENCH_OUT/repohard") / f"cursor_${safe}_repohard_latest.json"
if not p.is_file():
    print("MISSING")
    raise SystemExit(1)
rows = json.loads(p.read_text())
n = len(rows) if isinstance(rows, list) else 0
s = sum(int(r.get("score") or 0) for r in rows) if n else 0
m = sum(int(r.get("max_score") or 0) for r in rows) if n else 0
print(f"{p.name}: n={n} score={s}/{m}")
if n < 8:
    raise SystemExit(1)
PY
      ;;
    *)
      echo "unknown bench $bench"
      return 1
      ;;
  esac
}

run_one() {
  local model="$1" bench="$2"
  local safe tag
  safe="$(safe_name "$model")"
  export BENCH_MODEL="$model"
  echo "---- start $(date) model=$model bench=$bench (post-harness) ----"
  case "$bench" in
    pyhard)
      tag="cursor_${safe}_pyhard"
      BENCH_TAG="$tag" "$PY" -u "$ROOT/run.py" run pyhard \
        || echo "WARN pyhard failed model=$model"
      ;;
    arch)
      tag="cursor_${safe}_arch"
      BENCH_TAG="$tag" "$PY" -u "$ROOT/run.py" run arch \
        || echo "WARN arch failed model=$model"
      ;;
    claim)
      tag="cursor_${safe}_claim"
      BENCH_TAG="$tag" "$PY" -u "$ROOT/run.py" run claim \
        || echo "WARN claim failed model=$model"
      ;;
    repohard)
      tag="cursor_${safe}_repohard"
      git -C "$ROOT" checkout -- benches/repohard/fixture/ledgerkit/ || true
      BENCH_TAG="$tag" "$PY" -u "$ROOT/run.py" run repohard \
        || echo "WARN repohard failed model=$model"
      ;;
    *)
      echo "unknown bench $bench"
      return 1
      ;;
  esac
  if check_complete "$model" "$bench"; then
    echo "---- ok $(date) model=$model bench=$bench ----"
    return 0
  fi
  # Timeout/watchdog may have killed mid-suite — resume remaining via merge.
  if [[ "$bench" == "pyhard" || "$bench" == "arch" || "$bench" == "repohard" ]]; then
    echo "---- resume incomplete via BENCH_MERGE_LATEST $(date) model=$model bench=$bench ----"
    case "$bench" in
      pyhard)
        BENCH_TAG="cursor_${safe}_pyhard" BENCH_MERGE_LATEST=1 \
          "$PY" -u "$ROOT/run.py" run pyhard || echo "WARN pyhard resume failed"
        ;;
      arch)
        BENCH_TAG="cursor_${safe}_arch" BENCH_MERGE_LATEST=1 \
          "$PY" -u "$ROOT/run.py" run arch || echo "WARN arch resume failed"
        ;;
      repohard)
        git -C "$ROOT" checkout -- benches/repohard/fixture/ledgerkit/ || true
        BENCH_TAG="cursor_${safe}_repohard" BENCH_MERGE_LATEST=1 \
          "$PY" -u "$ROOT/run.py" run repohard || echo "WARN repohard resume failed"
        ;;
    esac
  fi
  if check_complete "$model" "$bench"; then
    echo "---- ok after resume $(date) model=$model bench=$bench ----"
  else
    echo "---- WARN incomplete $(date) model=$model bench=$bench ----"
  fi
}

echo "==== cursor gap queue start $(date) post-harness=1 from=${FROM:-beginning} ===="
echo "NOTE: scores from this queue are post-harness; do not mix with pre-fix latest without labels."

skipping=0
if [[ -n "$FROM" ]]; then
  skipping=1
fi

for job in "${JOBS[@]}"; do
  model="${job%%:*}"
  benches="${job#*:}"
  if (( skipping )); then
    if [[ "$model" == "$FROM" ]]; then
      skipping=0
    else
      echo "---- skip until --from: $model ----"
      continue
    fi
  fi
  echo "==== job $(date) model=$model benches=$benches ===="
  IFS=',' read -r -A bench_arr <<<"$benches"
  for bench in "${bench_arr[@]}"; do
    run_one "$model" "$bench"
  done
  echo "==== job done $(date) model=$model ===="
done

echo "==== cursor gap queue ALL DONE $(date) ===="
"$PY" -u "$ROOT/run.py" report --no-color || true
