---
description: Review code or architecture in stages, each one gated on its evidence
argument-hint: what to review, and how narrowly
---

review: $ARGUMENTS

Run this as the staged review flow. Launch `survey`, then `claims`, then `adversary`, each as a
subagent whose prompt begins with `STAGE: <name>` on its own first line, and wait for each to report
before launching the next. The rest of each prompt is supplied for you.

Do not do the reviewing yourself. When the last stage has reported, give me its surviving claims
with their evidence, and say plainly which ones the adversary stage could not test.
