---
id: alien_order
title: Hard: alien dictionary order
max_score: 10
---

Write a Python 3.14 function with this exact signature:

def alien_order(words: list[str]) -> str:

words is a list of strings in sorted order for an unknown alphabet.
Derive a valid character order for that alphabet.

Return:
- a string containing each distinct letter from words exactly once, in a valid order
- "" if the input implies no valid order (cycle, or a longer word placed before its prefix)

If multiple orders are valid, any one is accepted.
Letters are lowercase a-z only.

After any reasoning, output ONE fenced python code block containing the function.
