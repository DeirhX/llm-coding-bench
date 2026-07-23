# Pyhard failure autopsy (per-case rescoring)

Official harness scored fails as **0** on first exception. Per-case reruns below.

## Rescored leaderboard

| Model | Official | Rescored | Delta |
|-------|----------|----------|-------|
| **qwen3-coder:30b-a3b-fp16** | 81/99 | **92/99** | +11 |
| **qwen3-coder-next:q8_0** | 81/99 | **88/99** | +7 |

Tied on binary pass/fail (7/9); **30B wins on partial credit**.

---

## qwen3-coder-next:q8_0 — rescored **88/99**

| Task | Official | Rescored | Notes |
|------|----------|----------|-------|
| regex_match … json_patch | 71/71 | 71/71 | clean |
| sat_solve | 10/10 | 10/10 | clean |
| unify | 0/10 | **7/10** | return-type bug |
| mini_sql | 0/8 | **0/8** | parser still total wipeout |
| **total** | 81/99 | **88/99** | |

### unify → 7/10

| Case | Result | What happened |
|------|--------|----------------|
| const_eq | soft✓ / API✗ | returns `True` instead of `{}` / env |
| var_const | ✓ | `{'X': 3}` |
| alias | ✓ | vars linked |
| fn_bind | ✗ | after unifying args, `update(True)` → TypeError |
| fn_nested | ✓ | single-arg path avoids the bool update |
| const_neq | ✗ | returns `False`, not `None` |
| fn_name / fn_arity / occurs | ✓ | |
| conflict | ✗ | same bool/None confusion in multi-arg fn |

**Root cause:** success for two constants is `return a == b` (bool). Spec wants an env dict / `None`. Recursive fn unification then does `current_env.update(result)` and explodes when `result is True`. Algorithm shape is fine; the contract is wrong.

### mini_sql → 0/8

| Case | Result | What happened |
|------|--------|----------------|
| where_* | ✗ | returns unfiltered rows (Bob included) |
| select_all | ✗ | returns `[]` |
| join_* | ✗ | wrong/empty results |

**Root causes (two bugs):**

1. `FROM` regex requires `\s+(JOIN|WHERE|$)` — so bare `FROM users` at EOL **does not match** (needs whitespace before `$`) → empty result for `SELECT id FROM users`.
2. When `WHERE` is present, that same group **consumes the WHERE keyword**, so `where_clause` is never parsed → filters ignored → all rows returned.

Not a SQL-semantics miss; it’s a regex that hates its own grammar.

---

## qwen3-coder:30b-a3b-fp16 — rescored **92/99**

| Task | Official | Rescored | Notes |
|------|----------|----------|-------|
| regex … fix_vm, json_patch, unify | 71/71 | 71/71 | clean (incl. unify 10/10) |
| sat_solve | 0/10 | **7/10** | polarity-destroying dedup |
| mini_sql | 0/8 | **4/8** | JOIN/WHERE parse |
| **total** | 81/99 | **92/99** | |

### sat_solve → 7/10

| Case | Result | What happened |
|------|--------|----------------|
| unit_pos / unit_neg | ✓ | |
| conflict `[[1],[-1]]` | ✗ | reports `{1: True}` |
| sat2 / sat3 / sat4 / tautology / unsat3 | ✓ | |
| unsat_extra / unsat_xor | ✗ | false SAT |

**Root cause:** dedup key is `tuple(sorted(abs(lit) for lit in clause))` — **drops sign**. `[[1],[-1]]` collapses to one clause. Completeness murdered in preprocess; the backtracker never sees the conflict.

### mini_sql → 4/8

| Case | Result | What happened |
|------|--------|----------------|
| where_eq / gt / ne | ✓ | single-table WHERE works |
| select_all | ✓ | |
| join_where / join_and / join_filter | ✗ | `ValueError: Invalid JOIN condition` |
| where_and | ✗ | `ValueError: Invalid WHERE condition` |

**Root causes:**

1. `JOIN ... ON (.+)` is greedy → swallows `WHERE ...` into the ON clause → `split('=')` gets ≠2 parts.
2. `WHERE age >= 30 AND name != 'Cy'`: condition splitter/`!=` handling chokes on the quoted string path (raises rather than filtering).

Single-table simple WHERE is fine; JOIN + compound WHERE is where it faceplants.

---

## Verdict

| | Next | 30B |
|--|------|-----|
| Hard suite (first 5) | perfect | perfect |
| sat_solve | perfect | 7/10 (unsound dedup) |
| unify | 7/10 (bool vs env) | perfect |
| mini_sql | 0/8 (FROM/WHERE regex) | 4/8 (JOIN greedy + AND) |
| **Rescored** | **88/99** | **92/99** |

Neither “failed the hard stuff.” They failed **parser/API edge contracts**:

- Next: type-contract bug in unify + catastrophic SQL lexer
- 30B: clever-but-wrong SAT preprocess + greedy JOIN regex

If you only trust binary task pass/fail, they look tied. Partial credit says **30B is ahead**, and Next’s mini_sql is the only true total wipeout.
