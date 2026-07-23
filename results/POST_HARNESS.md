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
