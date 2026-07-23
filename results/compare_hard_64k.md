# Hard bench compare @ 64k ctx / 16k predict (MoEs)

| Task | Qwen3-Coder-Next Q8 | Qwen3-Coder 30B-A3B FP16 |
|------|------|------|
| regex_match | PASS 12/12 (9.9s, 69.4 t/s) | PASS 12/12 (7.0s, 65.9 t/s) |
| lru_cache | PASS 14/14 (8.6s, 69.6 t/s) | PASS 14/14 (6.6s, 65.9 t/s) |
| alien_order | PASS 10/10 (9.4s, 69.4 t/s) | PASS 10/10 (9.8s, 66.3 t/s) |
| eval_expr | PASS 12/12 (8.9s, 69.3 t/s) | PASS 12/12 (7.5s, 66.7 t/s) |
| fix_vm | PASS 10/10 (52.4s, 68.3 t/s) | FAIL 0/10 (0.0s, 0.0 t/s) |

- **Qwen3-Coder-Next Q8**: 58/58, wall 89.3s, ~69.2 tok/s, tasks 5/5
- **Qwen3-Coder 30B-A3B FP16**: 48/58, wall 31.0s, ~66.2 tok/s, tasks 4/5

Note: 30B `fix_vm` score is from post-hoc audit; official run hung in grader (no execution timeout).
