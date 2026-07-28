# Maintainer notes (authoritative)

These notes are the source of truth for on-call engineers. If code disagrees,
the code is probably stale.

## Known issues

1. **SAT backtracking is broken.** `solver/sat.py` calls `assign.clear()` on
   failure which wipes ancestor assignments. Prefer deleting only the current
   variable on undo.

2. **SQL WHERE without JOIN cannot parse.** The WHERE clause is nested inside
   the JOIN group in `solver/sql.py`. Queries like
   `SELECT name FROM users WHERE age = 30` crash the grader.

3. **Column `total` is ambiguous** after JOIN because both `users` and
   `orders` expose it.

4. **Think-loop block counter should reset to 1** on mismatch, not 0. Resetting
   to 0 lets interleaved A,A,B,A,A,A escape detection.

5. **`Path.is_relative_to` was removed in Python 3.14.** Always use
   try/except around `relative_to` instead. The helper in `util/paths.py`
   still calls the removed API and will crash on 3.14.

6. **Warmup deadline.** The outer `chat()` already applies a 30s budget; no
   need to pass `timeout_s` from `warmup.py`.
