#!/bin/zsh
# Wait for universal matrix ALL DONE, then start ollama post-harness queue.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/results/universal_matrix/wait_then_post.log"
mkdir -p "$ROOT/results/universal_matrix"
: >>"$LOG"
exec >>"$LOG" 2>&1

echo "==== waiter start $(date) ===="
MATRIX_LOG="$ROOT/results/universal_matrix/matrix.log"
PIDF="$ROOT/results/universal_matrix/matrix.pid"

for i in $(seq 1 2000); do
  if grep -q 'universal matrix ALL DONE' "$MATRIX_LOG" 2>/dev/null; then
    echo "matrix done marker found $(date)"
    break
  fi
  if [[ -f "$PIDF" ]]; then
    pid=$(cat "$PIDF")
    if ! ps -p "$pid" >/dev/null 2>&1; then
      # process died — check if completed or crashed
      if grep -q 'universal matrix ALL DONE' "$MATRIX_LOG" 2>/dev/null; then
        echo "matrix done after pid exit $(date)"
        break
      fi
      echo "WARN matrix pid dead without ALL DONE $(date); starting post-harness anyway"
      break
    fi
  fi
  sleep 30
done

echo "==== launching post-harness queue $(date) ===="
chmod +x "$ROOT/scripts/run_ollama_post_harness_queue.sh"
# Daemonize so Cursor/job-control can't reap the queue when this waiter exits.
"$ROOT/.venv/bin/python" - <<PY
import subprocess
from pathlib import Path
root = Path("$ROOT")
proc = subprocess.Popen(
    ["/bin/zsh", str(root / "scripts/run_ollama_post_harness_queue.sh")],
    cwd=str(root),
    start_new_session=True,
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
(root / "results/ollama_post_harness_queue.pid").write_text(str(proc.pid) + "\n")
print("post-harness pid=", proc.pid)
PY
echo "==== waiter done $(date) ===="
