---
id: eval_expr
title: Hard: arithmetic expression evaluator
max_score: 12
---

Write a Python 3.14 function with this exact signature:

def eval_expr(expr: str) -> int:

Evaluate a string arithmetic expression and return an int.

Grammar / rules:
- Integers may be negative (unary minus) and multi-digit
- Binary operators: + - * /
- Parentheses ( ) for grouping
- No exponentiation
- Spaces may appear anywhere and must be ignored
- Operator precedence: * and / bind tighter than + and -
- Same-precedence operators associate left-to-right
- Division truncates toward zero (e.g. (-7)/2 == -3)
- expr is always syntactically valid for these tests

After any reasoning, output ONE fenced python code block containing the function.
