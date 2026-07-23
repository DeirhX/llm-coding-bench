#!/usr/bin/env python3.14
"""Harder coding bench — models write Python, graded under CPython 3.14.

Usage:
  python run.py run pyhard
  BENCH_MODEL='qwen3-coder-next:q8_0' BENCH_TAG='next_pyhard' python -m benches.pyhard
  BENCH_PROVIDER=cursor BENCH_MODEL='composer-2.5' python -m benches.pyhard
  BENCH_SELFTEST=1 python -m benches.pyhard
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bench_lib.assignment import load_markdown_assignment  # noqa: E402
from bench_lib.paths import results_dir  # noqa: E402

OUT_DIR = results_dir()
_ASSIGN_DIR = Path(__file__).resolve().parent / "assignment"

SELFTEST = os.environ.get("BENCH_SELFTEST") == "1"
PROVIDER = os.environ.get("BENCH_PROVIDER", "ollama").strip().lower()
MODEL = "selftest" if SELFTEST else os.environ.get("BENCH_MODEL", "")
if not SELFTEST and not MODEL:
    raise SystemExit("Set BENCH_MODEL or BENCH_SELFTEST=1")
TAG = os.environ.get(
    "BENCH_TAG",
    "selftest_pyhard"
    if SELFTEST
    else f"{'cursor_' if PROVIDER == 'cursor' else ''}{re.sub(r'[^a-zA-Z0-9._-]', '_', MODEL)}",
)

# Prefer the interpreter running this file (should be 3.14).
PYTHON = sys.executable
if sys.version_info[:2] < (3, 14):
    raise SystemExit(f"Need Python >= 3.14 for grading, got {sys.version}")

OPTIONS = {
    "temperature": 0.1,
    "num_ctx": int(os.environ.get("BENCH_NUM_CTX", "65536")),
    "num_predict": int(os.environ.get("BENCH_NUM_PREDICT", "16384")),
}

GRADE_TIMEOUT_S = 5.0


@dataclass
class Task:
    id: str
    title: str
    max_score: int
    prompt: str
    grade: Callable[[str], dict[str, Any]]
    reference: str


def chat_ollama(model: str, prompt: str) -> dict[str, Any]:
    body = {
        "model": model,
        "stream": False,
        "messages": [{"role": "user", "content": prompt}],
        "options": OPTIONS,
    }
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=3600) as resp:
        data = json.loads(resp.read().decode())
    wall = time.perf_counter() - t0
    msg = data.get("message") or {}
    content = msg.get("content") or ""
    thinking = msg.get("thinking") or ""
    combined = content if not thinking else f"<think>\n{thinking}\n</think>\n{content}"
    eval_duration = float(data.get("eval_duration") or 0)
    eval_count = float(data.get("eval_count") or 0)
    return {
        "content": content,
        "thinking": thinking,
        "combined": combined,
        "wall_s": wall,
        "load_s": float(data.get("load_duration") or 0) / 1e9,
        "prompt_tokens": int(data.get("prompt_eval_count") or 0),
        "eval_tokens": int(data.get("eval_count") or 0),
        "toks_per_s": (eval_count / (eval_duration / 1e9)) if eval_duration > 0 else 0.0,
        "done_reason": data.get("done_reason"),
        "provider": "ollama",
    }


def chat_cursor(model: str, prompt: str) -> dict[str, Any]:
    from bench_lib import cursor_cli

    # Isolated empty workspace so ask-mode cannot trampoline into this repo.
    with tempfile.TemporaryDirectory(prefix="cursor_pyhard_") as td:
        return cursor_cli.chat(model, prompt, mode="ask", workspace=td)


def chat(model: str, prompt: str) -> dict[str, Any]:
    if PROVIDER in ("cursor", "cursor-cli", "agent"):
        return chat_cursor(model, prompt)
    if PROVIDER != "ollama":
        raise SystemExit(f"Unknown BENCH_PROVIDER={PROVIDER!r} (use ollama|cursor)")
    return chat_ollama(model, prompt)


def extract_python(text: str) -> str:
    fences = re.findall(r"```(?:python|py)?\s*\n([\s\S]*?)```", text, flags=re.I)
    if fences:
        return fences[-1].strip()
    for pat in (
        r"(class LRUCache\b[\s\S]*)",
        r"(def (?:is_match|alien_order|eval_expr|run_vm|sat_solve|apply_json_patch|unify|execute_select|compile_expr)\b[\s\S]*)",
    ):
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    return text.strip()


def run_fragment(code: str, timeout_s: float = GRADE_TIMEOUT_S) -> tuple[str, int]:
    with tempfile.TemporaryDirectory(prefix="pybench_") as td:
        path = Path(td) / "candidate.py"
        path.write_text(code, encoding="utf-8")
        try:
            proc = subprocess.run(
                [PYTHON, str(path)],
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            out = (proc.stdout or "") + (proc.stderr or "")
            return out, proc.returncode
        except subprocess.TimeoutExpired:
            return f"TIMEOUT after {timeout_s}s", -1


def score_cases(code: str, harness: str, default_max: int) -> dict[str, Any]:
    out, status = run_fragment(code + "\n" + harness)
    full = out.strip()
    # Prefer the SCORE line even when many FAIL lines precede it.
    m = re.search(r"SCORE (\d+)/(\d+)", full)
    detail = full[-1200:] if len(full) > 1200 else full
    if m:
        score, mx = int(m.group(1)), int(m.group(2))
        return {
            "ok": score == mx,
            "score": score,
            "max_score": mx,
            "detail": detail,
            "code": code,
        }
    return {
        "ok": False,
        "score": 0,
        "max_score": default_max,
        "detail": detail or f"exit={status}",
        "code": code,
    }


# --- references -------------------------------------------------------------

REF_REGEX = r'''
def is_match(s: str, p: str) -> bool:
    m, n = len(s), len(p)
    dp = [[False] * (n + 1) for _ in range(m + 1)]
    dp[0][0] = True
    for j in range(1, n + 1):
        if p[j - 1] == "*" and j >= 2 and dp[0][j - 2]:
            dp[0][j] = True
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if p[j - 1] == "*":
                dp[i][j] = dp[i][j - 2]
                if p[j - 2] == "." or p[j - 2] == s[i - 1]:
                    dp[i][j] = dp[i][j] or dp[i - 1][j]
            elif p[j - 1] == "." or p[j - 1] == s[i - 1]:
                dp[i][j] = dp[i - 1][j - 1]
    return dp[m][n]
'''.strip()

REF_LRU = r'''
class LRUCache:
    class _N:
        __slots__ = ("key", "val", "prev", "next")
        def __init__(self, key=None, val=None):
            self.key, self.val = key, val
            self.prev = self.next = None

    def __init__(self, capacity: int):
        self.cap = capacity
        self.map = {}
        self.head = self._N()
        self.tail = self._N()
        self.head.next = self.tail
        self.tail.prev = self.head

    def get(self, key: int) -> int:
        n = self.map.get(key)
        if n is None:
            return -1
        self._move(n)
        return n.val

    def put(self, key: int, value: int) -> None:
        if key in self.map:
            n = self.map[key]
            n.val = value
            self._move(n)
            return
        if len(self.map) >= self.cap:
            lru = self.tail.prev
            self._remove(lru)
            del self.map[lru.key]
        n = self._N(key, value)
        self.map[key] = n
        self._insert(n)

    def _remove(self, n):
        n.prev.next = n.next
        n.next.prev = n.prev

    def _insert(self, n):
        n.next = self.head.next
        n.prev = self.head
        self.head.next.prev = n
        self.head.next = n

    def _move(self, n):
        self._remove(n)
        self._insert(n)
'''.strip()

REF_ALIEN = r'''
from collections import defaultdict, deque

def alien_order(words: list[str]) -> str:
    chars = set()
    for w in words:
        chars.update(w)
    graph = defaultdict(set)
    indeg = {c: 0 for c in chars}
    for i in range(len(words) - 1):
        a, b = words[i], words[i + 1]
        if len(a) > len(b) and a.startswith(b):
            return ""
        for x, y in zip(a, b):
            if x != y:
                if y not in graph[x]:
                    graph[x].add(y)
                    indeg[y] += 1
                break
    q = deque(sorted(c for c, d in indeg.items() if d == 0))
    order = []
    while q:
        c = q.popleft()
        order.append(c)
        for nxt in sorted(graph[c]):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                q.append(nxt)
                q = deque(sorted(q))
    return "".join(order) if len(order) == len(chars) else ""
'''.strip()

REF_EVAL = r'''
def eval_expr(expr: str) -> int:
    s = "".join(expr.split())
    i = 0

    def factor() -> int:
        nonlocal i
        if s[i] == "+":
            i += 1
            return factor()
        if s[i] == "-":
            i += 1
            return -factor()
        if s[i] == "(":
            i += 1
            v = expression()
            i += 1
            return v
        start = i
        while i < len(s) and s[i].isdigit():
            i += 1
        return int(s[start:i])

    def term() -> int:
        nonlocal i
        v = factor()
        while i < len(s) and s[i] in "*/":
            op = s[i]
            i += 1
            r = factor()
            v = v * r if op == "*" else int(v / r)  # toward zero
        return v

    def expression() -> int:
        nonlocal i
        v = term()
        while i < len(s) and s[i] in "+-":
            op = s[i]
            i += 1
            r = term()
            v = v + r if op == "+" else v - r
        return v

    return expression()
'''.strip()

REF_VM = r'''
def run_vm(code: list, inputs: list[int]) -> int:
    ip = 0
    stack: list[int] = []
    in_i = 0
    while ip < len(code):
        op, *args = code[ip]
        ip += 1
        if op == "IN":
            stack.append(inputs[in_i])
            in_i += 1
        elif op == "PUSH":
            stack.append(args[0])
        elif op == "ADD":
            b, a = stack.pop(), stack.pop()
            stack.append(a + b)
        elif op == "SUB":
            b, a = stack.pop(), stack.pop()
            stack.append(a - b)
        elif op == "MUL":
            b, a = stack.pop(), stack.pop()
            stack.append(a * b)
        elif op == "DUP":
            stack.append(stack[-1])
        elif op == "SWAP":
            stack[-1], stack[-2] = stack[-2], stack[-1]
        elif op == "JZ":
            v = stack.pop()
            if v == 0:
                ip += args[0]
        elif op == "JMP":
            ip += args[0]
        elif op == "HALT":
            return stack[-1] if stack else 0
        else:
            raise ValueError(f"unknown op {op}")
    return stack[-1] if stack else 0
'''.strip()

REF_SAT = r'''
def sat_solve(n: int, clauses: list[list[int]]) -> dict[int, bool] | None:
    assign = {}

    def value(lit: int) -> bool | None:
        v = abs(lit)
        if v not in assign:
            return None
        return assign[v] if lit > 0 else (not assign[v])

    def simplify(cls):
        out = []
        for c in cls:
            sat = False
            nc = []
            for lit in c:
                val = value(lit)
                if val is True:
                    sat = True
                    break
                if val is None:
                    nc.append(lit)
            if sat:
                continue
            if not nc:
                return None
            out.append(nc)
        return out

    def dpll(cls):
        while True:
            s = simplify(cls)
            if s is None:
                return False
            cls = s
            unit = next((c[0] for c in cls if len(c) == 1), None)
            if unit is None:
                break
            assign[abs(unit)] = unit > 0
        if not cls:
            for v in range(1, n + 1):
                assign.setdefault(v, False)
            return True
        # pick var
        lit = cls[0][0]
        v = abs(lit)
        for bit in (True, False):
            assign[v] = bit
            snap = dict(assign)
            if dpll([c[:] for c in cls]):
                return True
            assign.clear()
            assign.update(snap)
            del assign[v]
        return False

    ok = dpll([c[:] for c in clauses])
    if not ok:
        return None
    return {v: bool(assign.get(v, False)) for v in range(1, n + 1)}
'''.strip()

REF_JSON_PATCH = r'''
def apply_json_patch(doc, ops: list[dict]):
    import copy
    root = copy.deepcopy(doc)

    def tokens(path: str):
        if path == "":
            return []
        if not path.startswith("/"):
            raise ValueError("bad path")
        out = []
        for part in path[1:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            out.append(part)
        return out

    def parent_get(obj, parts):
        cur = obj
        for p in parts[:-1]:
            if isinstance(cur, list):
                cur = cur[int(p)]
            else:
                cur = cur[p]
        return cur, parts[-1] if parts else None

    def get(obj, path):
        parts = tokens(path)
        if not parts:
            return obj
        cur, last = parent_get(obj, parts)
        if isinstance(cur, list):
            return cur[int(last)]
        return cur[last]

    for op in ops:
        kind = op["op"]
        path = op["path"]
        parts = tokens(path)
        if kind == "test":
            if get(root, path) != op["value"]:
                raise ValueError("test failed")
        elif kind == "add":
            if not parts:
                root = copy.deepcopy(op["value"])
                continue
            cur, last = parent_get(root, parts)
            if isinstance(cur, list):
                if last == "-":
                    cur.append(copy.deepcopy(op["value"]))
                else:
                    cur.insert(int(last), copy.deepcopy(op["value"]))
            else:
                cur[last] = copy.deepcopy(op["value"])
        elif kind == "remove":
            cur, last = parent_get(root, parts)
            if isinstance(cur, list):
                del cur[int(last)]
            else:
                del cur[last]
        elif kind == "replace":
            cur, last = parent_get(root, parts)
            if isinstance(cur, list):
                cur[int(last)] = copy.deepcopy(op["value"])
            else:
                cur[last] = copy.deepcopy(op["value"])
        elif kind == "move":
            val = get(root, op["from"])
            # remove from
            fparts = tokens(op["from"])
            fcur, flast = parent_get(root, fparts)
            if isinstance(fcur, list):
                val = fcur.pop(int(flast))
            else:
                val = fcur.pop(flast)
            # add to path
            if not parts:
                root = val
            else:
                cur, last = parent_get(root, parts)
                if isinstance(cur, list):
                    if last == "-":
                        cur.append(val)
                    else:
                        cur.insert(int(last), val)
                else:
                    cur[last] = val
        else:
            raise ValueError(f"unsupported op {kind}")
    return root
'''.strip()

REF_UNIFY = r'''
def unify(a, b, env=None):
    if env is None:
        env = {}

    def deref(t):
        while isinstance(t, tuple) and t and t[0] == "var" and t[1] in env:
            t = env[t[1]]
        return t

    def occurs(name, t):
        t = deref(t)
        if isinstance(t, tuple) and t and t[0] == "var":
            return t[1] == name
        if isinstance(t, tuple) and t and t[0] == "fn":
            return any(occurs(name, arg) for arg in t[2])
        return False

    def u(x, y):
        x, y = deref(x), deref(y)
        if x == y:
            return True
        if isinstance(x, tuple) and x and x[0] == "var":
            if occurs(x[1], y):
                return False
            env[x[1]] = y
            return True
        if isinstance(y, tuple) and y and y[0] == "var":
            if occurs(y[1], x):
                return False
            env[y[1]] = x
            return True
        if isinstance(x, tuple) and isinstance(y, tuple) and x and y and x[0] == "fn" and y[0] == "fn":
            if x[1] != y[1] or len(x[2]) != len(y[2]):
                return False
            return all(u(p, q) for p, q in zip(x[2], y[2]))
        return False

    return env if u(a, b) else None
'''.strip()

REF_SQL = r'''
import re

def execute_select(tables: dict, query: str):
    q = " ".join(query.strip().split())
    m = re.match(
        r"SELECT (.+?) FROM (\w+)(?: JOIN (\w+) ON (\w+)\.(\w+) = (\w+)\.(\w+))?(?: WHERE (.+))?$",
        q,
        flags=re.I,
    )
    if not m:
        raise ValueError("unsupported SQL")
    cols_s, t1, t2, a_t, a_c, b_t, b_c, where = m.groups()
    cols = [c.strip() for c in cols_s.split(",")]

    def resolve(rowmap, name):
        if "." in name:
            t, c = name.split(".", 1)
            return rowmap[t][c]
        # bare column: unique among present tables
        hits = []
        for t, row in rowmap.items():
            if name in row:
                hits.append(row[name])
        if len(hits) != 1:
            raise KeyError(name)
        return hits[0]

    rows = []
    if t2:
        for r1 in tables[t1]:
            for r2 in tables[t2]:
                rm = {t1: r1, t2: r2}
                left_t, right_t = a_t, b_t
                if rm[left_t][a_c] == rm[right_t][b_c]:
                    rows.append(rm)
    else:
        for r1 in tables[t1]:
            rows.append({t1: r1})

    def eval_where(rm, expr):
        parts = re.split(r"\s+AND\s+", expr, flags=re.I)
        for part in parts:
            mm = re.match(r"(.+?)\s*(=|!=|>=|<=|>|<)\s*(.+)$", part.strip())
            left_s, op, right_s = mm.groups()
            left_s, right_s = left_s.strip(), right_s.strip()
            def atom(s):
                if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
                    return s[1:-1]
                if re.fullmatch(r"-?\d+", s):
                    return int(s)
                return resolve(rm, s)
            l, r = atom(left_s), atom(right_s)
            ok = {
                "=": l == r, "!=": l != r, ">": l > r, "<": l < r, ">=": l >= r, "<=": l <= r,
            }[op]
            if not ok:
                return False
        return True

    if where:
        rows = [rm for rm in rows if eval_where(rm, where)]

    out = []
    for rm in rows:
        item = {}
        for c in cols:
            key = c.split(".")[-1] if "." in c else c
            # if duplicate bare names, keep last — tests use unique aliases via table.col keys as given
            item[c] = resolve(rm, c)
            # also expose short key when unique
            item[key] = item[c]
        # normalize to selected names only
        out.append({c: resolve(rm, c) for c in cols})
    return out
'''.strip()


# --- graders ----------------------------------------------------------------

def grade_regex_match(text: str) -> dict[str, Any]:
    code = extract_python(text)
    harness = r'''
assert callable(is_match)
cases = [
    ("aa", "a", False), ("aa", "a*", True), ("ab", ".*", True), ("aab", "c*a*b", True),
    ("mississippi", "mis*is*p*.", False), ("", "", True), ("", "a*", True), ("a", "ab*", True),
    ("bbbba", ".*a*a", True), ("ab", ".*c", False), ("aaa", "a*a", True), ("aaa", "aaaa", False),
]
pass_n = 0
for i, (s, p, exp) in enumerate(cases):
    try:
        got = is_match(s, p)
        assert bool(got) is bool(exp), f"case {i}: {got!r} != {exp!r}"
        pass_n += 1
    except Exception as _e:
        print(f"FAIL case {i}: {_e}")
print(f"SCORE {pass_n}/{len(cases)}")
'''
    return score_cases(code, harness, 12)


def grade_lru_cache(text: str) -> dict[str, Any]:
    code = extract_python(text)
    harness = r'''
assert "LRUCache" in globals()
checks = []
c = LRUCache(2)
c.put(1, 1); c.put(2, 2)
checks.append(c.get(1) == 1)
c.put(3, 3)
checks.append(c.get(2) == -1)
checks.append(c.get(3) == 3)
c.put(4, 4)
checks.append(c.get(1) == -1)
checks.append(c.get(3) == 3)
checks.append(c.get(4) == 4)
c2 = LRUCache(2)
c2.put(2, 1); c2.put(2, 2)
checks.append(c2.get(2) == 2)
c2.put(1, 1); c2.put(4, 1)
checks.append(c2.get(2) == -1)
c3 = LRUCache(1)
c3.put(2, 1)
checks.append(c3.get(2) == 1)
c3.put(3, 2)
checks.append(c3.get(2) == -1)
checks.append(c3.get(3) == 2)
c4 = LRUCache(2)
c4.put(1, 1); c4.put(2, 2); c4.get(1); c4.put(3, 3)
checks.append(c4.get(2) == -1)
checks.append(c4.get(3) == 3)
checks.append(c4.get(1) == 1)
pass_n = 0
for i, ok in enumerate(checks):
    try:
        assert ok, f"check {i+1} failed"
        pass_n += 1
    except Exception as _e:
        print(f"FAIL case {i}: {_e}")
print(f"SCORE {pass_n}/{len(checks)}")
'''
    return score_cases(code, harness, 14)


def grade_alien_order(text: str) -> dict[str, Any]:
    code = extract_python(text)
    harness = r'''
from collections import defaultdict

def implied(words):
    chars = set("".join(words))
    edges = set()
    invalid = False
    for i in range(len(words) - 1):
        a, b = words[i], words[i + 1]
        if len(a) > len(b) and a.startswith(b):
            invalid = True
            break
        for x, y in zip(a, b):
            if x != y:
                edges.add((x, y))
                break
    return chars, edges, invalid

def valid_order(order, words):
    if not isinstance(order, str):
        return False
    chars, edges, invalid = implied(words)
    if invalid:
        return order == ""
    if order == "":
        return False
    if sorted(order) != sorted(chars) or len(set(order)) != len(order):
        return False
    pos = {c: i for i, c in enumerate(order)}
    return all(pos[a] < pos[b] for a, b in edges)

cases = [
    (["wrt", "wrf", "er", "ett", "rftt"], "valid"),
    (["z", "x"], "valid"),
    (["z", "x", "z"], "invalid"),
    (["abc", "ab"], "invalid"),
    (["a", "b", "ca", "cc"], "valid"),
    (["ac", "ab", "bc"], "valid"),
    (["a"], "valid"),
    (["ab", "adc"], "valid"),
    (["abc", "abx", "ag"], "valid"),
    (["z", "z"], "valid"),
]
pass_n = 0
for i, (words, kind) in enumerate(cases):
    try:
        got = alien_order(list(words))
        ok = (got == "") if kind == "invalid" else valid_order(got, words)
        assert ok, f"case {i}: {got!r}"
        pass_n += 1
    except Exception as _e:
        print(f"FAIL case {i}: {_e}")
print(f"SCORE {pass_n}/{len(cases)}")
'''
    return score_cases(code, harness, 10)


def grade_eval_expr(text: str) -> dict[str, Any]:
    code = extract_python(text)
    harness = r'''
cases = [
    ("3+2*2", 7), (" 3+5 / 2 ", 5), ("(1+(4+5+2)-3)+(6+8)", 23), ("2-1+2", 3),
    ("-2+3", 1), ("1-(2+3)", -4), ("14/3*2", 8), ("(-7)/2", -3),
    ("2*(3+4)*5", 70), ("10-2-3", 5), ("8/2/2", 2), ("-((2+3)*4)", -20),
]
pass_n = 0
for i, (expr, exp) in enumerate(cases):
    try:
        got = eval_expr(expr)
        assert got == exp, f"case {i}: {got!r} != {exp!r}"
        pass_n += 1
    except Exception as _e:
        print(f"FAIL case {i}: {_e}")
print(f"SCORE {pass_n}/{len(cases)}")
'''
    return score_cases(code, harness, 12)


def grade_fix_vm(text: str) -> dict[str, Any]:
    code = extract_python(text)
    harness = r'''
cases = [
    ([["IN"], ["IN"], ["SUB"], ["HALT"]], [5, 3], 2),
    ([["PUSH", 2], ["PUSH", 7], ["PUSH", 4], ["SUB"], ["MUL"], ["HALT"]], [], 6),
    ([["IN"], ["DUP"], ["ADD"], ["HALT"]], [9], 18),
    ([["IN"], ["IN"], ["SWAP"], ["SUB"], ["HALT"]], [3, 10], 7),
    ([["PUSH", 0], ["JZ", 1], ["PUSH", 99], ["PUSH", 7], ["HALT"]], [], 7),
    ([["PUSH", 1], ["JZ", 1], ["PUSH", 99], ["HALT"]], [], 99),
    ([["JMP", 1], ["PUSH", 1], ["PUSH", 2], ["HALT"]], [], 2),
    ([
        ["PUSH", 0], ["IN"], ["DUP"], ["JZ", 2], ["ADD"], ["JMP", -5], ["SWAP"], ["HALT"],
    ], [3, 4, 5, 0], 12),
    ([["HALT"]], [], 0),
    ([["PUSH", 40], ["PUSH", 2], ["ADD"], ["HALT"]], [], 42),
]
pass_n = 0
for i, (prog, inputs, exp) in enumerate(cases):
    try:
        got = run_vm([list(op) for op in prog], list(inputs))
        assert got == exp, f"case {i}: {got!r} != {exp!r}"
        pass_n += 1
    except Exception as _e:
        print(f"FAIL case {i}: {_e}")
print(f"SCORE {pass_n}/{len(cases)}")
'''
    return score_cases(code, harness, 10)


def grade_sat_solve(text: str) -> dict[str, Any]:
    code = extract_python(text)
    harness = r'''
def ok_assign(n, clauses, asg):
    if asg is None:
        return False
    if set(asg) != set(range(1, n + 1)):
        return False
    for c in clauses:
        if not any((asg[abs(l)] if l > 0 else (not asg[abs(l)])) for l in c):
            return False
    return True

cases = [
    (1, [[1]], True),
    (1, [[-1]], True),
    (1, [[1], [-1]], False),
    (2, [[1, 2], [-1, 2], [1, -2]], True),
    (3, [[1, 2], [-1, 3], [-2, -3], [1, -2, 3]], True),
    (2, [[1], [-1], [2]], False),
    (3, [[1, 2, 3], [-1], [-2], [-3]], False),
    (4, [[1, 2], [3, 4], [-1, -3], [-2, -4], [1, 4]], True),
    (2, [[1, -1], [2]], True),
    (3, [[1, 2], [1, -2], [-1, 2], [-1, -2]], False),
]
pass_n = 0
for i, (n, clauses, sat) in enumerate(cases):
    try:
        got = sat_solve(n, [c[:] for c in clauses])
        if sat:
            assert ok_assign(n, clauses, got), f"case {i}: bad assign {got!r}"
        else:
            assert got is None, f"case {i}: expected None got {got!r}"
        pass_n += 1
    except Exception as _e:
        print(f"FAIL case {i}: {_e}")
print(f"SCORE {pass_n}/{len(cases)}")
'''
    return score_cases(code, harness, 10)


def grade_json_patch(text: str) -> dict[str, Any]:
    code = extract_python(text)
    harness = r'''
cases = []
# 1 add
cases.append(({"a": 1}, [{"op": "add", "path": "/b", "value": 2}], {"a": 1, "b": 2}))
# 2 remove
cases.append(({"a": 1, "b": 2}, [{"op": "remove", "path": "/b"}], {"a": 1}))
# 3 replace
cases.append(({"a": 1}, [{"op": "replace", "path": "/a", "value": 9}], {"a": 9}))
# 4 nested add
cases.append(({"a": {"b": 1}}, [{"op": "add", "path": "/a/c", "value": 3}], {"a": {"b": 1, "c": 3}}))
# 5 array append
cases.append(({"x": [1, 2]}, [{"op": "add", "path": "/x/-", "value": 3}], {"x": [1, 2, 3]}))
# 6 array insert
cases.append(({"x": [1, 3]}, [{"op": "add", "path": "/x/1", "value": 2}], {"x": [1, 2, 3]}))
# 7 array remove
cases.append(({"x": [1, 2, 3]}, [{"op": "remove", "path": "/x/1"}], {"x": [1, 3]}))
# 8 escape ~
cases.append(({"a/b": 1}, [{"op": "add", "path": "/m~0n", "value": 2}], {"a/b": 1, "m~n": 2}))
# 9 escape slash key add via ~1
cases.append(({}, [{"op": "add", "path": "/a~1b", "value": 1}], {"a/b": 1}))
# 10 move
cases.append(({"a": 1, "b": {"c": 2}}, [{"op": "move", "from": "/a", "path": "/b/d"}], {"b": {"c": 2, "d": 1}}))
# 11 test ok then replace
cases.append(({"a": 1}, [{"op": "test", "path": "/a", "value": 1}, {"op": "replace", "path": "/a", "value": 2}], {"a": 2}))
# 12 multi-op
cases.append(({"q": {"w": [0]}}, [{"op": "add", "path": "/q/w/-", "value": 1}, {"op": "remove", "path": "/q/w/0"}], {"q": {"w": [1]}}))

pass_n = 0
for i, (doc, ops, exp) in enumerate(cases):
    try:
        got = apply_json_patch(doc, ops)
        assert got == exp, f"case {i}: {got!r} != {exp!r}"
        pass_n += 1
    except Exception as _e:
        print(f"FAIL case {i}: {_e}")
# test failure should raise (must not credit a silent no-op)
try:
    apply_json_patch({"a": 1}, [{"op": "test", "path": "/a", "value": 0}])
except Exception:
    pass_n += 1
else:
    print("FAIL case test-raise: test should have raised")
print(f"SCORE {pass_n}/13")
'''
    return score_cases(code, harness, 13)


def grade_unify(text: str) -> dict[str, Any]:
    code = extract_python(text)
    harness = r'''
def deep_subst(t, env):
    if isinstance(t, tuple) and t and t[0] == "var":
        return deep_subst(env[t[1]], env) if t[1] in env else t
    if isinstance(t, tuple) and t and t[0] == "fn":
        return ("fn", t[1], [deep_subst(a, env) for a in t[2]])
    return t

cases_ok = [
    (1, 1, {}),
    (("var", "X"), 3, {"X": 3}),
    (("var", "X"), ("var", "Y"), "alias"),
    (("fn", "f", [("var", "X"), 1]), ("fn", "f", [2, 1]), {"X": 2}),
    (("fn", "f", [("var", "X")]), ("fn", "f", [("fn", "g", [1])]), {"X": ("fn", "g", [1])}),
]
cases_bad = [
    (1, 2),
    (("fn", "f", [1]), ("fn", "g", [1])),
    (("fn", "f", [1]), ("fn", "f", [1, 2])),
    (("var", "X"), ("fn", "f", [("var", "X")])),  # occurs
    (("fn", "f", [("var", "X"), ("var", "X")]), ("fn", "f", [1, 2])),
]
pass_n = 0
for i, item in enumerate(cases_ok):
    try:
        a, b, exp = item
        got = unify(a, b)
        assert isinstance(got, dict), f"ok case {i}: expected dict got {type(got).__name__}: {got!r}"
        if exp == "alias":
            assert deep_subst(("var", "X"), got) == deep_subst(("var", "Y"), got)
        else:
            for k, v in exp.items():
                assert deep_subst(("var", k), got) == deep_subst(v, got), f"ok case {i} env"
        pass_n += 1
    except Exception as _e:
        print(f"FAIL ok case {i}: {_e}")
for i, (a, b) in enumerate(cases_bad):
    try:
        got = unify(a, b)
        assert got is None, f"bad case {i}: {got!r}"
        pass_n += 1
    except Exception as _e:
        print(f"FAIL bad case {i}: {_e}")
print(f"SCORE {pass_n}/{len(cases_ok)+len(cases_bad)}")
'''
    return score_cases(code, harness, 10)


def grade_sql(text: str) -> dict[str, Any]:
    code = extract_python(text)
    harness = r'''
tables = {
    "users": [
        {"id": 1, "name": "Ann", "age": 30},
        {"id": 2, "name": "Bob", "age": 20},
        {"id": 3, "name": "Cy", "age": 30},
    ],
    "orders": [
        {"id": 10, "user_id": 1, "total": 50},
        {"id": 11, "user_id": 1, "total": 5},
        {"id": 12, "user_id": 2, "total": 40},
        {"id": 13, "user_id": 3, "total": 40},
    ],
}
cases = [
    ("SELECT name FROM users WHERE age = 30", [{"name": "Ann"}, {"name": "Cy"}]),
    ("SELECT name FROM users WHERE age > 25", [{"name": "Ann"}, {"name": "Cy"}]),
    ("SELECT id FROM users WHERE name != 'Bob'", [{"id": 1}, {"id": 3}]),
    (
        "SELECT users.name, orders.total FROM users JOIN orders ON users.id = orders.user_id WHERE orders.total >= 40",
        [
            {"users.name": "Ann", "orders.total": 50},
            {"users.name": "Bob", "orders.total": 40},
            {"users.name": "Cy", "orders.total": 40},
        ],
    ),
    (
        "SELECT orders.id FROM users JOIN orders ON users.id = orders.user_id WHERE users.name = 'Ann' AND orders.total < 10",
        [{"orders.id": 11}],
    ),
    ("SELECT id FROM users", [{"id": 1}, {"id": 2}, {"id": 3}]),
    (
        "SELECT users.id, orders.id FROM users JOIN orders ON users.id = orders.user_id WHERE users.age = 20",
        [{"users.id": 2, "orders.id": 12}],
    ),
    (
        "SELECT name FROM users WHERE age >= 30 AND name != 'Cy'",
        [{"name": "Ann"}],
    ),
]
pass_n = 0
for i, (q, exp) in enumerate(cases):
    try:
        got = execute_select(tables, q)
        assert got == exp, f"case {i}: {got!r} != {exp!r}"
        pass_n += 1
    except Exception as _e:
        print(f"FAIL case {i}: {_e}")
print(f"SCORE {pass_n}/{len(cases)}")
'''
    return score_cases(code, harness, 8)


BUGGY_VM_PROMPT = r'''
This Python stack VM is supposed to evaluate a tiny bytecode language, but it has bugs.

Instruction format: a list of ops. Each op is [opname, *args].
Stack holds ints. Inputs come from a list consumed left-to-right by IN.

Ops:
- ["IN"]           push next input value
- ["PUSH", n]      push integer n
- ["ADD"]          pop b, pop a, push a+b
- ["SUB"]          pop b, pop a, push a-b
- ["MUL"]          pop b, pop a, push a*b
- ["DUP"]          duplicate top of stack
- ["SWAP"]         swap top two stack values
- ["JZ", offset]   pop v; if v == 0, add offset to IP
                   (IP has already been advanced past this instruction; offset is relative)
- ["JMP", offset]  add offset to IP (same relative rule as JZ)
- ["HALT"]         stop; return top of stack (or 0 if empty)

Buggy implementation:

```python
def run_vm(code, inputs):
    ip = 0
    stack = []
    in_i = 0
    while ip < len(code):
        op, *args = code[ip]
        ip += 1
        if op == "IN":
            stack.append(inputs[in_i])
        elif op == "PUSH":
            stack.append(args[0])
        elif op == "ADD":
            a = stack.pop()
            b = stack.pop()
            stack.append(a + b)
        elif op == "SUB":
            a = stack.pop()
            b = stack.pop()
            stack.append(a - b)
        elif op == "MUL":
            a = stack.pop()
            b = stack.pop()
            stack.append(a * b)
        elif op == "DUP":
            stack.append(stack[-1])
        elif op == "SWAP":
            stack[-1], stack[-2] = stack[-2], stack[-1]
        elif op == "JZ":
            v = stack.pop()
            if v != 0:
                ip += args[0]
        elif op == "JMP":
            ip += args[0]
        elif op == "HALT":
            return stack[-1] if stack else 0
        else:
            raise ValueError(f"unknown op {op}")
    return stack[-1] if stack else 0
```

Find and fix ALL bugs. Keep the same function signature and op names.
Do not leave bug comments in your solution.
After any reasoning, output ONE fenced python code block containing the corrected function.
'''

def _prompt(task_id: str) -> tuple[str, int, str]:
    """Load title, max_score, prompt body from assignment/<id>.md."""
    meta, body = load_markdown_assignment(_ASSIGN_DIR / f"{task_id}.md")
    return str(meta["title"]), int(meta["max_score"]), body


def _task(
    task_id: str,
    grade: Callable[[str], dict[str, Any]],
    reference: str,
) -> Task:
    title, max_score, prompt = _prompt(task_id)
    return Task(
        id=task_id,
        title=title,
        max_score=max_score,
        prompt=prompt,
        grade=grade,
        reference=reference,
    )


# Prompts live in assignment/*.md — this list is the grader/reference registry only.
TASKS: list[Task] = [
    _task("regex_match", grade_regex_match, REF_REGEX),
    _task("lru_cache", grade_lru_cache, REF_LRU),
    _task("alien_order", grade_alien_order, REF_ALIEN),
    _task("eval_expr", grade_eval_expr, REF_EVAL),
    _task("fix_vm", grade_fix_vm, REF_VM),
    _task("sat_solve", grade_sat_solve, REF_SAT),
    _task("json_patch", grade_json_patch, REF_JSON_PATCH),
    _task("unify", grade_unify, REF_UNIFY),
    _task("mini_sql", grade_sql, REF_SQL),
]


def log(path: Path, s: str) -> None:
    print(s, end="")
    with path.open("a", encoding="utf-8") as f:
        f.write(s)


def main() -> None:
    if SELFTEST:
        print(f"Self-test Python hard bench @ {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"grader: {PYTHON} ({sys.version.split()[0]})")
        all_ok = True
        for task in TASKS:
            g = task.grade(f"```python\n{task.reference}\n```")
            status = "PASS" if g["ok"] else "FAIL"
            print(f"{status} {task.id} {g['score']}/{g['max_score']} — {g['detail'].splitlines()[0] if g['detail'] else ''}")
            if not g["ok"]:
                print(g["detail"])
            all_ok = all_ok and g["ok"]
        # buggy VM must fail
        buggy = BUGGY_VM_PROMPT.split("```python")[1].split("```")[0]
        bg = grade_fix_vm(f"```python\n{buggy}\n```")
        print(f"buggy VM should fail: score={bg['score']} ok={bg['ok']}")
        all_ok = all_ok and (not bg["ok"])
        if not all_ok:
            raise SystemExit("SELFTEST FAILED")
        print("SELFTEST OK")
        return

    log_path = OUT_DIR / f"{TAG}_pyhard.log"
    summary_path = OUT_DIR / f"{TAG}_pyhard_{time.strftime('%Y%m%d_%H%M%S')}.json"
    log_path.write_text("", encoding="utf-8")
    log(log_path, f"Python hard bench {time.strftime('%Y-%m-%d %H:%M:%S %z')}\n")
    log(
        log_path,
        f"provider={PROVIDER}\nmodel={MODEL}\npython={PYTHON}\nversion={sys.version}\n"
        f"options={OPTIONS}\n",
    )

    try:
        warm = chat(MODEL, "Reply with exactly: OK")
        extra = ""
        if PROVIDER == "ollama":
            with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=30) as resp:
                ps = json.loads(resp.read().decode())
            m = next(
                (
                    x
                    for x in ps.get("models") or []
                    if x.get("name") == MODEL or x.get("model") == MODEL
                ),
                None,
            )
            size = (m["size"] / 2**30) if m else 0
            ctx = m.get("context_length", "?") if m else "?"
            extra = f" ctx={ctx} size_gib={size:.1f}"
        log(
            log_path,
            f"warmup ok wall={warm['wall_s']:.1f}s load={warm['load_s']:.1f}s "
            f"eval_tokens={warm['eval_tokens']}{extra}\n",
        )
    except Exception as e:
        log(log_path, f"warmup ERROR: {e}\n")

    results: list[dict[str, Any]] = []
    for task in TASKS:
        log(log_path, f"\n-- {task.id} ...\n")
        try:
            resp = chat(MODEL, task.prompt)
            g = task.grade(resp["combined"])
            mx = g["max_score"] or task.max_score
            row = {
                "model": MODEL,
                "provider": PROVIDER,
                "task": task.id,
                "title": task.title,
                "ok": g["ok"],
                "score": g["score"],
                "max_score": mx,
                "grade_detail": g["detail"],
                "wall_s": round(resp["wall_s"], 2),
                "load_s": round(resp["load_s"], 2),
                "eval_tokens": resp["eval_tokens"],
                "prompt_tokens": resp["prompt_tokens"],
                "toks_per_s": round(resp["toks_per_s"], 2),
                "done_reason": resp["done_reason"],
                "content_chars": len(resp["content"]),
                "thinking_chars": len(resp["thinking"]),
                "code_chars": len(g.get("code") or ""),
                "num_ctx": OPTIONS["num_ctx"],
                "num_predict": OPTIONS["num_predict"],
                "python": sys.version.split()[0],
            }
            results.append(row)
            (OUT_DIR / f"{TAG}__{task.id}.txt").write_text(resp["combined"], encoding="utf-8")
            (OUT_DIR / f"{TAG}__{task.id}__code.py").write_text(g.get("code") or "", encoding="utf-8")
            log(log_path, json.dumps(row, indent=2) + "\n")
        except Exception as e:
            row = {
                "model": MODEL,
                "task": task.id,
                "ok": False,
                "score": 0,
                "max_score": task.max_score,
                "error": str(e),
            }
            results.append(row)
            log(log_path, f"ERROR: {e}\n")
        summary_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    total = sum(r.get("score", 0) for r in results)
    mx = sum(r.get("max_score", 0) for r in results)
    passed = sum(1 for r in results if r.get("ok"))
    tps = [r["toks_per_s"] for r in results if r.get("toks_per_s")]
    avg_tps = sum(tps) / len(tps) if tps else 0.0
    log(
        log_path,
        f"\n===== PYHARD {MODEL} =====\n"
        f"pass {passed}/{len(results)}  score {total}/{mx}  avg tok/s {avg_tps:.1f}\n"
        f"Wrote {summary_path}\n",
    )
    (OUT_DIR / f"{TAG}_pyhard_latest.json").write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
