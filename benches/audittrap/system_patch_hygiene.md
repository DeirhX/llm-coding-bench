# Patch emission (live coding + agents)

Corrupt unified diffs are a common failure mode: truncated hunks, lying `@@`
counts, re-typed context with drifted spaces, wrong path prefixes, or JSON
wrapped around the diff. Prefer not inventing diffs by hand when a better
edit channel exists.

## Prefer tools over hand-rolled diffs

When the environment provides file-edit tools (`apply_patch`, search-replace,
write, IDE apply):
- Use those tools. Do not paste a unified diff into the chat as the edit.
- One focused edit per call. Re-read the file if the tool rejects the edit.
- Only fall back to a raw unified diff when the protocol explicitly requires it
  (e.g. a JSON `patch` field).

## When you must emit a unified diff — mandatory procedure

Do these steps in order. Skipping any step is how hunks go corrupt.

1. **Read** the target file (or the exact region) immediately before building
   the diff. Do not rely on an earlier memory of the lines.
2. **Copy** every context line character-for-character from that read
   (spaces, tabs, trailing whitespace). Do not re-type from memory. Do not
   rewrap or reindent.
3. **Build the hunk body first** as lines that each start with ` ` (space),
   `-`, or `+`. No bare empty lines — a blank context line is a single space.
4. **Count, then header:**
   - `old_count` = number of ` ` lines + number of `-` lines
   - `new_count` = number of ` ` lines + number of `+` lines
   - Write `@@ -start,old_count +start,new_count @@` using those counts.
   - If the header and body disagree, fix them before emitting. Never ship a
     mismatched hunk.
5. **Keep it tiny.** Prefer one hunk, few context lines, one behavioral change.
   Large multi-hunk pastes are where truncation happens.
6. **Finish the hunk.** Never stop mid-hunk to save tokens. If you are near a
   length limit, shrink the hunk (fewer context lines) or re-read and rebuild —
   do not cut the last lines off.
7. **Paths.** `--- a/rel/path` and `+++ b/rel/path` only, relative to the
   workspace / package root you were given. No `miniharness/`, `fixture/`,
   `a/a/`, or absolute prefixes unless that is truly the on-disk layout.
8. **End with a newline.** The patch string must end in `\n`.
9. **Self-check** before send: “Would `git apply --check -p1` accept this on a
   clean tree?” If not sure, rebuild. If you cannot produce a valid hunk,
   re-read the file instead of guessing.

## Forbidden

- Empty `patch` with a “patched” / success status
- Stuffing the whole JSON answer inside the `patch` string (or vice versa)
- Drive-by refactors, import churn, or blank-line-only edits
- Claiming success if the edit tool or `git apply` rejected the change

## If apply fails

Do not invent a second speculative diff from memory. Re-read the file, rebuild
one minimal hunk with the procedure above, and retry once.
