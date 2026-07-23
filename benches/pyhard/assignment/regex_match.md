---
id: regex_match
title: Hard: regex match with . and *
max_score: 12
---

Write a Python 3.14 function with this exact signature:

def is_match(s: str, p: str) -> bool:

Implement full-string matching where pattern p supports only:
- letters matching themselves
- '.' matching any single character
- '*' meaning "zero or more of the preceding element"

The match must cover the entire string s (not a substring).
s and p contain only lowercase letters, '.', and '*'.
Assume every '*' has a valid preceding element.

After any reasoning, output ONE fenced python code block containing the function.
