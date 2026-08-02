"""One implementation of "does this local run get a system message, and which one".

Three benches needed this and audittrap had it; the other two had no way to receive a system
prompt at all, which made the depth contract unmeasurable on exactly the benches whose scores it
must not damage.

The defaults differ on purpose and the difference is historical, not aesthetic. audittrap passes
``system_local.md`` by default because the 63 words in it are the whole difference between 0/20 and
20/20 on false-bug traps, and every audittrap number on record was measured with it. arch and claim
have no default, because every arch and claim number on record was measured *without* one, and
switching one on by default would silently re-baseline both suites.

Cursor runs are excluded by the caller, not here: the Cursor CLI composes its own system prompt and
a second one arrives as a user turn, which is a different experiment.
"""

from __future__ import annotations

import os
from pathlib import Path


def local_system_prompt(default_path: str | Path | None = None) -> str | None:
    """The system message for an Ollama/OpenAI-local run, or None.

    ``BENCH_SYSTEM_PROMPT=0`` disables it; ``BENCH_SYSTEM_PROMPT_FILE`` names the file.

    Naming a file is itself a request for a system prompt, so it switches the feature on for the
    benches that default to off. The first version required both variables, and setting only the
    file gave a run that looked prompted and was not -- the same defect this repository has already
    hit twice, once in the launcher and once in a whole session that ran with no skepticism prompt
    while reporting one.

    A named file that does not exist is fatal for the same reason. A missing *default* file is not:
    that path has always been best-effort, and every audittrap score on record was measured with
    that leniency in place.
    """
    named = os.environ.get("BENCH_SYSTEM_PROMPT_FILE")
    raw = os.environ.get("BENCH_SYSTEM_PROMPT", "1" if (default_path or named) else "0")
    if raw.strip().lower() in ("0", "false", "off", "no", ""):
        return None
    if named and not Path(named).is_file():
        raise SystemExit("BENCH_SYSTEM_PROMPT_FILE names a file that does not exist: %s" % named)
    configured = named or default_path
    if not configured:
        return None
    path = Path(configured)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def initial_messages(user_prompt: str, system: str | None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_prompt})
    return messages
