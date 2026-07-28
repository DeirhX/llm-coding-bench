---
title: Ctrl-C recorded as task failure
family: repair
max_score: 10
---

# Ctrl-C recorded as task failure

On-call report: interrupting a long suite with Ctrl-C leaves a scored failure
row for the in-flight task (`ok: false`, `done_reason: error`) instead of
stopping the process. Operators want a clean interrupt.
