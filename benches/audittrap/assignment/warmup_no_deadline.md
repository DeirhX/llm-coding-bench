---
title: Warmup ignores short timeout
family: repair
max_score: 10
---

# Warmup ignores short timeout

Incident: cold-start warmup can sit for many minutes against a wedged backend.
Operators expected the warmup path to fail fast (on the order of tens of
seconds), not inherit the suite first-byte budget.
