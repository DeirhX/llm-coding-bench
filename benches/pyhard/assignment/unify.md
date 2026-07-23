---
id: unify
title: Harder: term unification + occurs check
max_score: 10
---

Write a Python 3.14 function with this exact signature:

def unify(a, b, env=None) -> dict | None:

Unify two terms. Terms are:
- ints or strings (constants)
- ("var", name) variables, name is a str
- ("fn", name, args) function terms, args is a list of terms

Rules:
- Mutually recursive through env substitutions
- Occurs-check: a variable must not unify with a term containing itself
- On success return a dict env mapping variable names to terms (may be indirect)
- On failure return None
- If env is provided, update/extend it; if None, start with {}

After any reasoning, output ONE fenced python code block containing the function.
