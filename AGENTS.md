When editing Python files, **never use the `edit` tool with hand-typed indented string blocks**.

**Root cause:** When indentation appears inside my tool `oldString`/`newString` parameters,
leading whitespace is stripped by the tool system. A line like `    code` (4 spaces)
arrives at the tool as `code` (0 spaces). Nested blocks compound: 8→0, 12→0.
This silently corrupts indentation in every `edit` that touches indented code.

**Rule: Always use `bash` with a heredoc to write/replace Python code.**

```bash
cat > /path/to/file.py << 'PYTHON_EOF'
code with indentation preserved byte-for-byte
    indented_line = 1
        nested = 2
PYTHON_EOF
```

The single-quoted delimiter (`'PYTHON_EOF'`) prevents all shell expansion
and preserves whitespace exactly. Never use `edit` for multi-line Python
insertions or replacements.
