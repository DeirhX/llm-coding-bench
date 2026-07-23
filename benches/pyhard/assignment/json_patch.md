---
id: json_patch
title: Harder: JSON Patch (RFC6902 subset)
max_score: 13
---

Write a Python 3.14 function with this exact signature:

def apply_json_patch(doc, ops: list[dict]):

Apply a JSON Patch (RFC 6902 subset) and return the resulting document.
Support ops: add, remove, replace, move, test.
Paths use JSON Pointer (/a/b/0), with ~0 => ~ and ~1 => /.
For arrays, "-" appends on add; numeric segments are indices.
add into an object sets/replaces that key; add into a list inserts at index.
move is remove-from + add-to (use "from" and "path").
test must raise on mismatch.
Do not mutate the input document; return a new/updated structure.

After any reasoning, output ONE fenced python code block containing the function.
