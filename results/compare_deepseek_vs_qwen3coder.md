# DeepSeek vs Qwen3-Coder (64k ctx / 16k predict)

| Task | DeepSeek-R1 70B Q8 | Qwen3-Coder 30B-A3B FP16 |
|------|--------------------|---------------------------|
| merge_intervals | PASS 7/7 (321.51s, 6.41 t/s, 2057 tok) | PASS 7/7 (2.41s, 65.12 t/s, 143 tok) |
| bug_binary_search | PASS 2/2 (96.72s, 6.79 t/s, 646 tok) | PASS 2/2 (2.64s, 63.85 t/s, 152 tok) |
| course_schedule | PASS 7/7 (353.95s, 7.04 t/s, 2484 tok) | PASS 7/7 (7.41s, 63.57 t/s, 458 tok) |

## Totals
- DeepSeek: **16/16**, wall **772.2s**, ~6.7 tok/s
- Qwen3-Coder: **16/16**, wall **12.5s**, ~64.2 tok/s

## Quality
- Correctness: **tied** (both perfect on this suite).
- DeepSeek uses long chain-of-thought (thousands of thinking chars) then lands correct code; Qwen emits code directly with ~10x fewer tokens.
- On this benchmark, Qwen3-Coder matches quality at ~10x wall-clock and ~10x token efficiency. DeepSeek's extra reasoning did not buy extra points here.
- Caveat: suite is small/synthetic; DeepSeek may still help on messier real debugging where CoT matters.
