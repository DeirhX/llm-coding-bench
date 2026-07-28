---
title: Timed-out child with stderr-only output loses diagnostics
family: repair
max_score: 10
---

# Timed-out child with stderr-only output loses diagnostics

When a subprocess is killed on wall-clock timeout after writing only to stderr,
callers get a bare timeout exception and never see the stderr dump. Jobs that
printed progress to stdout before hanging still surface a killed
`CompletedProcess`.
