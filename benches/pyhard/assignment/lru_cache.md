---
id: lru_cache
title: Hard: LRU cache class
max_score: 14
---

Write a Python 3.14 class with this exact API:

class LRUCache:
    def __init__(self, capacity: int): ...
    def get(self, key: int) -> int: ...   # value, or -1 if missing
    def put(self, key: int, value: int) -> None: ...

Semantics:
- capacity is a positive int
- get/put of an existing key makes it most-recently used
- put of a new key when at capacity evicts the least-recently used key
- put may update an existing key's value (and mark it most-recently used)
- keys and values are ints

After any reasoning, output ONE fenced python code block containing the full class.
