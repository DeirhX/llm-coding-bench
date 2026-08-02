#!/usr/bin/env python3
"""Status line for Claude Code sessions run against a local model.

Auto-compaction does not work here and cannot be made to: the threshold path is behind the
remote feature gate tengu_sepia_moth, which defaults to false and is never fetched because this
machine has no Anthropic credential, and the reactive path only fires when the API reports the
prompt as too long, which Ollama never does -- it silently grows the KV cache instead. Both
compactions in this project's history were triggered by hand, at 109,754 and 116,875 tokens,
long past the point where a turn stays affordable.

So the human is the trigger, and a human needs a number in front of them. Claude Code hands a
status line command a context_window block on stdin, so the count is the client's own rather
than an estimate. It still understates the truth by whatever the tool schemas and chat template
add -- measured at 4,733 tokens with the lean tool set, 18,009 with all of them -- which is why
the launcher declares a window smaller than the model's and this line is read against that.
"""

import json
import sys

RESET = "\033[0m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD_RED = "\033[1;31m"

# Thresholds set from measurement, not taste. Four compactions are on record: three above the
# runner's window cost 311s, 528s and 666s, decoding 2-5x below this machine's own rate curve
# because MLX grows the KV cache past its allocation and the box pages. The one from inside the
# window, at 57,876 tokens, cost 187s for a *longer* summary. So the useful signal is not "the
# window is nearly full" but "you have passed the point where compacting is cheap", which lands
# near 60% of the declared window. Red at 75% is the last comfortable moment; the runner's real
# window is behind that with the framing the client never counts.
NAG_PCT = 60.0
CRITICAL_PCT = 75.0


def humanise(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print("claude-gemma")
        return

    model = (payload.get("model") or {}).get("display_name") \
        or (payload.get("model") or {}).get("id") or "?"
    parts = [f"{DIM}{model}{RESET}"]
    nag = ""

    cw = payload.get("context_window") or {}
    used = cw.get("total_input_tokens")
    size = cw.get("context_window_size")
    pct = cw.get("used_percentage")

    if isinstance(used, (int, float)) and isinstance(size, int) and size > 0:
        if not isinstance(pct, (int, float)):
            pct = used / size * 100
        if pct >= CRITICAL_PCT:
            colour, nag = BOLD_RED, f"{BOLD_RED}/compact NOW{RESET}"
        elif pct >= NAG_PCT:
            colour, nag = RED, f"{RED}/compact{RESET}"
        elif pct >= NAG_PCT * 0.8:
            colour = YELLOW
        else:
            colour = GREEN
        parts.append(f"{colour}{humanise(used)}/{humanise(size)} {pct:.0f}%{RESET}")
    else:
        parts.append(f"{DIM}context unknown{RESET}")

    effort = (payload.get("effort") or {}).get("level")
    if effort:
        parts.append(f"{DIM}{effort}{RESET}")

    if nag:
        parts.append(nag)

    print(f" {DIM}·{RESET} ".join(parts))


if __name__ == "__main__":
    main()
