---
title: Chat timeout_s never reaches HTTP client
family: repair
max_score: 10
---

# Chat timeout_s never reaches HTTP client

Callers pass `timeout_s` into the public chat entrypoint, but the HTTP layer
appears to always see `None` (observed via the client's recorded timeout).
Code that sets a deadline still hangs for the default budget.
