# Post-harness Cursor gap campaign

**Started:** 2026-07-23 · queue log: `results/cursor_gap_queue.log` · pid: `results/cursor_gap_queue.pid`

## Do not mix eras

Scores written by `scripts/run_cursor_gap_queue.sh` are **post-harness**. Older
`*_latest.json` / timestamped JSON from before this campaign are **pre-harness**.

| Suite | Pre-harness gotcha | Post-harness change |
|---|---|---|
| repohard | Cursor workspace = canonical fixture (poison risk); patches often truncated in results | Per-task temp workspace; full `answer.patch` |
| claim | 15 T/F (`correct=15`, `max_score=18`) | 20 T/F (`max_score≈23`) |
| arch | Evidence lists hardcoded; invariant missed `invoice_service.py` | `required_files` from assignment frontmatter |
| pyhard | No env-extend unify case; no bare-col JOIN SQL case | Those cases live in the grader (still /99) |

## Queue jobs (in order)

1. Midtier: gemini-3.6-flash-high, cursor-grok-4.5-medium, gpt-5.4-mini-high
2. Frontier gaps: sol / terra / luna / haiku / opus / grok-4.5-high (pyhard→arch→claim)
3. Claim refresh: composer-2.5, sonnet-5-high, sonnet-5-thinking-high
4. Repohard leftovers: gpt-5.3-codex, sonnet-5-thinking-high

## Ranking rule

When a model has both eras for the same suite, prefer **post-harness** for ranking
and keep the timestamped pre-harness file for audit. Never average the two.

## Local Ollama post-harness (queued after universal matrix)

**Waiter:** `results/universal_matrix/waiter.pid` → starts
`scripts/run_ollama_post_harness_queue.sh` when the matrix logs `ALL DONE`.

**Log:** `results/ollama_post_harness_queue.log`

Still on pre-harness **claim** (`max_score=18`, 15 T/F) and thus scheduled for
claim re-run (and missing suite cells): qwen3.6/3.5, qwen3-coder(-next),
qwen2.5-coder, gpt-oss, north-mini, devstral, llama3.3, deepseek-r1.

Think knobs follow the matrix winner when `COMPARISON.md` exists; else think-off.

## Cursor claim zeros (forced)

Gap queue treated `n_per_claim>=20` as done even when all answers were missing
(`composer-2.5` / `claude-4.5-haiku` at 0/23). Forced re-run:

- Script: `scripts/run_cursor_claim_zeros.sh`
- Log: `results/cursor_claim_zeros.log`
- Pid: `results/cursor_claim_zeros.pid`

Root cause of the lingering 0/23: `parse_final` greedy-brace bug (models answered
in a ` ```json ` fence; parser ate trailing prose). Fixed in `benches/claim/bench.py`.

**Remote refresh (safe beside local matrix):** `scripts/run_cursor_remote_refresh.sh`
rescored claim from `raw_content` + arch evidence rescore; skipped Cursor repohard
while Ollama matrix holds `ledgerkit`. Log: `results/cursor_remote_refresh/remote.log`.
Composer/haiku claim now **20/23** (evidence bonus 0 in ask-mode).

**Cursor repohard stale (pre-isolation → post-isolation):** 8 models lacked
`answer.patch` in latest JSON. Queue waits for universal matrix `ALL DONE`, then
re-runs full Cursor repohard:

- Script: `scripts/run_cursor_repohard_stale.sh`
- Log: `results/repohard/cursor_repohard_stale.log`
- Pid: `results/repohard/cursor_repohard_stale.pid`
- Models: composer-2.5, sonnet-5-high, opus, haiku, sol/terra/luna, grok-4.5-high,
  gemini-3.6-flash-high (force re-run; was already POST from gap queue)
