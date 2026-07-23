---
id: fix_vm
title: Hard: fix buggy stack VM (3 bugs)
max_score: 10
---

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
