---
title: SAT backtracking wipes ancestor assignments
family: repair
max_score: 10
---

# SAT backtracking wipes ancestor assignments

Report from grading failures: `solver/sat.py` calls `assign.clear()` on a failed
trial, which wipes bindings that should survive backtracking. Hard instances
that need both branches appear to be solved incorrectly.
