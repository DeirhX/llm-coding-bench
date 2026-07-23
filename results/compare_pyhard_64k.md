# Python 3.14 hard bench @ 64k / 16k (all models)

| Task | Qwen3-Coder-Next Q8 | Qwen3-Coder 30B-A3B FP16 | gpt-oss 120B | Qwen3.5 35B-A3B Coding BF16 | Qwen3.6 35B-A3B Coding BF16 | North Mini Code 1.0 BF16 |
|------|------|------|------|------|------|------|
| regex_match | PASS 12/12 | PASS 12/12 | PASS 12/12 | PASS 12/12 | FAIL 0/12 | PASS 12/12 |
| lru_cache | PASS 14/14 | PASS 14/14 | PASS 14/14 | FAIL 0/14 | PASS 14/14 | PASS 14/14 |
| alien_order | PASS 10/10 | PASS 10/10 | PASS 10/10 | PASS 10/10 | PASS 10/10 | PASS 10/10 |
| eval_expr | PASS 12/12 | PASS 12/12 | PASS 12/12 | PASS 12/12 | FAIL 0/12 | FAIL 0/12 |
| fix_vm | PASS 10/10 | PASS 10/10 | PASS 10/10 | FAIL 0/10 | FAIL 0/10 | PASS 10/10 |
| sat_solve | PASS 10/10 | FAIL 0/10 | FAIL 0/10 | PASS 10/10 | FAIL 0/10 | FAIL 0/10 |
| json_patch | PASS 13/13 | PASS 13/13 | PASS 13/13 | FAIL 0/13 | PASS 13/13 | FAIL 0/13 |
| unify | FAIL 0/10 | PASS 10/10 | PASS 10/10 | FAIL 0/10 | PASS 10/10 | PASS 10/10 |
| mini_sql | FAIL 0/8 | FAIL 0/8 | FAIL 0/8 | PASS 8/8 | PASS 8/8 | FAIL 0/8 |

- **Qwen3-Coder-Next Q8**: 81/99, wall 141.6s, ~71.6 tok/s, pass 7/9
- **Qwen3-Coder 30B-A3B FP16**: 81/99, wall 83.0s, ~68.5 tok/s, pass 7/9
- **gpt-oss 120B**: 81/99, wall 197.4s, ~79.1 tok/s, pass 7/9
- **Qwen3.5 35B-A3B Coding BF16**: 52/99, wall 1406.8s, ~67.5 tok/s, pass 5/9
- **Qwen3.6 35B-A3B Coding BF16**: 55/99, wall 2257.4s, ~59.5 tok/s, pass 5/9
- **North Mini Code 1.0 BF16**: 56/99, wall 1545.5s, ~59.2 tok/s, pass 5/9


# Hi-budget rerun (num_predict=49152)

- **Qwen3.6 Coding BF16 @49k**: 57/99, pass 5/9, length_hits=4
  - regex_match: PASS 12/12 reason=stop
  - lru_cache: PASS 14/14 reason=length
  - alien_order: PASS 10/10 reason=length
  - eval_expr: FAIL 0/12 reason=length
  - fix_vm: FAIL 0/10 reason=length
  - sat_solve: FAIL 0/10 reason=stop
  - json_patch: PASS 13/13 reason=stop
  - unify: FAIL 0/10 reason=stop
  - mini_sql: PASS 8/8 reason=stop
- **North Mini Code BF16 @49k**: 91/99, pass 8/9, length_hits=0
  - regex_match: PASS 12/12 reason=stop
  - lru_cache: PASS 14/14 reason=stop
  - alien_order: PASS 10/10 reason=stop
  - eval_expr: PASS 12/12 reason=stop
  - fix_vm: PASS 10/10 reason=stop
  - sat_solve: PASS 10/10 reason=stop
  - json_patch: PASS 13/13 reason=stop
  - unify: PASS 10/10 reason=stop
  - mini_sql: FAIL 0/8 reason=stop


# Pyhard re-run after archbench (num_predict=16384)

- **Next Q8 re-run**: 77/99, pass 7/9, wall=151.0s  (qwen3-coder-next_q8_0_pyhard_rerun_pyhard_latest.json)
  - regex_match: PASS 12/12 reason=stop wall=10.2
  - lru_cache: PASS 14/14 reason=stop wall=6.31
  - alien_order: PASS 10/10 reason=stop wall=9.65
  - eval_expr: FAIL 0/12 reason=stop wall=10.41
  - fix_vm: PASS 10/10 reason=stop wall=30.68
  - sat_solve: PASS 10/10 reason=stop wall=18.23
  - json_patch: PASS 13/13 reason=stop wall=26.86
  - unify: FAIL 0/10 reason=stop wall=11.68
  - mini_sql: PASS 8/8 reason=stop wall=26.99
- **30B-A3B FP16 re-run**: 81/99, pass 7/9, wall=107.7s  (qwen3-coder_30b-a3b-fp16_pyhard_rerun_pyhard_latest.json)
  - regex_match: PASS 12/12 reason=stop wall=6.75
  - lru_cache: PASS 14/14 reason=stop wall=2.89
  - alien_order: PASS 10/10 reason=stop wall=8.83
  - eval_expr: PASS 12/12 reason=stop wall=12.2
  - fix_vm: PASS 10/10 reason=stop wall=8.71
  - sat_solve: FAIL 0/10 reason=stop wall=20.1
  - json_patch: PASS 13/13 reason=stop wall=19.62
  - unify: PASS 10/10 reason=stop wall=8.2
  - mini_sql: FAIL 0/8 reason=stop wall=20.39
