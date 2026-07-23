
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
