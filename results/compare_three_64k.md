# Compare @ 64k ctx / 16k predict

| Task | DeepSeek-R1 70B Q8 | Qwen3-Coder 30B-A3B FP16 | Qwen3-Coder-Next Q8 |
|------|------|------|------|
| merge_intervals | PASS 7/7 (321.5s, 6.4 t/s, 2057 tok) | PASS 7/7 (2.4s, 65.1 t/s, 143 tok) | PASS 7/7 (3.8s, 69.8 t/s, 243 tok) |
| bug_binary_search | PASS 2/2 (96.7s, 6.8 t/s, 646 tok) | PASS 2/2 (2.6s, 63.8 t/s, 152 tok) | PASS 2/2 (1.8s, 70.4 t/s, 103 tok) |
| course_schedule | PASS 7/7 (354.0s, 7.0 t/s, 2484 tok) | PASS 7/7 (7.4s, 63.6 t/s, 458 tok) | PASS 7/7 (6.1s, 70.1 t/s, 413 tok) |

- **DeepSeek-R1 70B Q8**: 16/16, wall 772.2s, ~6.7 tok/s
- **Qwen3-Coder 30B-A3B FP16**: 16/16, wall 12.5s, ~64.2 tok/s
- **Qwen3-Coder-Next Q8**: 16/16, wall 11.8s, ~70.1 tok/s
