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

## Before touching a live model session, read `LOCAL_AGENT_OPS.md`

It records what this setup costs and how it misleads you, all of it measured here and none of it
documented upstream. The three that will bite fastest:

- **Only one 31B runner fits, and `-64k`/`-96k`/`-128k` are different models to Ollama.** Naming a
  different variant than a live session evicts its prefix cache and costs both sides ~4 minutes
  per turn. Never start a probe against a variant the user is not already running.
- **A reload is ~6 s of weights and up to 5 minutes of lost cache.** Judge turn cost from
  `cache hit total=/matched=` in `~/.ollama/logs/server.log`, not from decode speed.
- **Requests are logged on completion, and no client identity is ever logged.** Causation must be
  established before the incident, by interposing the proxy — not after.
