---
id: mini_sql
title: Harder: mini SQL SELECT/JOIN/WHERE
max_score: 8
---

Write a Python 3.14 function with this exact signature:

def execute_select(tables: dict, query: str) -> list[dict]:

tables maps table name -> list of row dicts (column->value).
Support a tiny SQL subset (single line, keywords case-insensitive):

SELECT col[, col...] FROM t1
  [JOIN t2 ON t1.col = t2.col]
  [WHERE cond (AND cond...)]

Where each cond is: <atom> <op> <atom>
ops: = != > < >= <=
atom: table.col OR bare col (must be unambiguous among present tables) OR integer OR 'string'

JOIN is inner join only.
Return a list of dicts whose keys are exactly the selected column expressions
as written in the SELECT list (e.g. "users.name", "total").
Preserve left-to-right nested-loop row order: for t1 rows in order, for t2 rows in order.
WHERE filters after the join (or after FROM if no join).

After any reasoning, output ONE fenced python code block containing the function.
