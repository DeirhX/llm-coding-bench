---
id: sat_solve
title: Harder: CNF SAT solver
max_score: 10
---

Write a Python 3.14 function with this exact signature:

def sat_solve(n: int, clauses: list[list[int]]) -> dict[int, bool] | None:

Solve a CNF SAT instance.
- Variables are integers 1..n
- Each clause is a list of ints (literals); negative means negated variable
- Return a dict {1: bool, ..., n: bool} for any satisfying assignment
- Return None if unsatisfiable
- Empty clause is false; a clause with both x and -x is always true

After any reasoning, output ONE fenced python code block containing the function.
