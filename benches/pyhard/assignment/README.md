# Pyhard assignments

One markdown file per task. Frontmatter: `id`, `title`, `max_score`.
Graders and reference solutions live in `bench.py` (`grade_*`, `REF_*`).

Suite max is the sum of per-task `max_score` values (**99** across 9 tasks).
Partial credit comes from the grader harness (`SCORE passed/total`), not binary pass/fail.

| id | max |
|---|---:|
| regex_match | 12 |
| lru_cache | 14 |
| alien_order | 10 |
| eval_expr | 12 |
| fix_vm | 10 |
| sat_solve | 10 |
| json_patch | 13 |
| unify | 10 |
| mini_sql | 8 |

Assignment claims must match what the grader actually tests. If you change cases in
`grade_*`, update the corresponding `assignment/<id>.md` in the same change.
