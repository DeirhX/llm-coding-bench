
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
