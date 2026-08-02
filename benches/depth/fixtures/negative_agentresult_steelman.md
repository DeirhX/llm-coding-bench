This is the same proposal restated in the ledger form, with its citations made correct
and byte-exact. It exists to check that the gate refuses this argument on its substance rather
than on its formatting: given perfect quotes, the case for the rewrite is still that some strings
repeat, with nothing run and no caller examined.

CLAIM: Every agent builds its result dictionary from bare string keys rather than a type.
EVIDENCE: bench_lib/cursor_cli.py:175-182
QUOTE:
    return {
        "content": result,
        "thinking": "",
        "combined": result,
        "wall_s": wall,
        "load_s": 0.0,
        "prompt_tokens": prompt_tokens,
        "eval_tokens": eval_tokens,

CLAIM: The toks_per_s calculation is duplicated in each agent rather than computed in one place.
EVIDENCE: bench_lib/ollama_chat.py:310-313
QUOTE:
            "load_s": float(data.get("load_duration") or 0) / 1e9,
            "prompt_tokens": int(data.get("prompt_eval_count") or 0),
            "eval_tokens": int(data.get("eval_count") or 0),
            "toks_per_s": (eval_count / (eval_duration / 1e9)) if eval_duration > 0 else 0.0,

CLAIM: Configuration is read through more than a hundred separate os.environ.get call sites.
EVIDENCE: command: grep -r "os.environ.get" . -> 117
