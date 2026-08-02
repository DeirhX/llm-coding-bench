---
description: Make a change in stages, with the plan committing to the failure it expects
argument-hint: the change to make, as specifically as you can put it
---

implement: $ARGUMENTS

Run this as the staged change flow. Launch `plan`, then `implement`, then `verify`, each as a
subagent whose prompt begins with `STAGE: <name>` on its own first line, and wait for each to report
before launching the next. The rest of each prompt is supplied for you.

Only the middle stage may change the tree. If the plan stage is refused, the others will not start:
rerun the plan and satisfy it rather than working around it. When the last stage has reported, show
me the failing run and the passing run it produced, and say whether the change is load-bearing.
