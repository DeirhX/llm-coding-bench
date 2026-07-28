---
title: SQL WHERE without JOIN fails to parse
family: repair
max_score: 10
---

# SQL WHERE without JOIN fails to parse

Report: queries like `SELECT name FROM users WHERE age = 30` do not match the
SELECT regex because the WHERE clause is nested inside the JOIN optional group
in `solver/sql.py`. JOIN+WHERE queries are said to work; WHERE-only does not.
