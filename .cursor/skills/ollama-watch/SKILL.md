---
name: ollama-watch
description: Inspect what a local Ollama server is doing, decide whether it is working or stuck, and free the GPU safely. Use when asked what Ollama is doing, whether a model is stuck, hung, looping or slow, why a model unloaded or reloaded, how to evict or unload a model, why prompts are re-prefilled every turn, or when watching an unattended benchmark or a live Claude Code session against Ollama.
---

# Watching Ollama

Answers three questions, in this order: what is resident, is it working or stuck, and
can it be evicted safely.

## Is it working or stuck?

```bash
python3 .cursor/skills/ollama-watch/scripts/state.py
```

One verdict, from evidence rather than a guess:

| Verdict | Means | Do |
|---|---|---|
| `PREFILLING` | prompt processing is advancing; rate and remaining time are shown | wait, and never evict if minutes remain |
| `BUSY (generating)` | no prefill, but the runner is burning CPU or the expiry sits in the past | wait |
| `IDLE (resident)` | loaded, nothing in flight | safe to evict |
| `NOTHING LOADED` | GPU is free | nothing to do |
| `LOOPING` (extra line) | the same prompt restarted from zero more than once | stop the **client**, not the model |

Slowness is not stuckness. Prefill runs at roughly 500 tokens/sec on a 62 GB model
with memory to spare, and around 150 tokens/sec when memory is exhausted, so a 100k
prompt legitimately takes minutes with no output. The rate and remaining time in the
output settle it; `--json` gives the same thing for scripting.

## Freeing the GPU

```bash
bash .cursor/skills/ollama-watch/scripts/evict.sh --dry-run   # what would happen
bash .cursor/skills/ollama-watch/scripts/evict.sh             # idle, no clients
bash .cursor/skills/ollama-watch/scripts/evict.sh --clients    # kill clients first
```

The order matters and is counter-intuitive:

1. `ollama stop` is **silently refused** while a request is in flight. It prints a
   spinner, exits 0, and the model stays with an expiry of `-0 min`.
2. Killing the runner works and is undone in about a second: the server respawns it
   for the client that is still retrying, reloads the weights, and restarts the same
   prefill from zero.
3. So the client goes first. `evict.sh` finds clients by socket on port 11434, not by
   process name, and refuses rather than guessing when it cannot tell.

## Unattended runs

```bash
screen -dmS ollamawatch python3 .cursor/skills/ollama-watch/scripts/watch.py
```

Reports only what warrants interrupting: cache restore failures, large prefills with
poor cache reuse, prompts within 5% of the window, silent truncation, a dead runner,
and swap crossing a threshold. Everything else is noise.

## Why a model keeps unloading

The Anthropic-compatible endpoint (`/v1/messages`) **ignores** `keep_alive` in the
request body; `/api/chat` honours it. So a warm-up that shows eight hours is knocked
back to the five-minute default by the first real turn. Fix it at the server, once:

```bash
scripts/ollama-keepalive.sh 8h        # sets OLLAMA_KEEP_ALIVE via launchctl, restarts Ollama
scripts/ollama-keepalive.sh --check   # probes what a request with no keep_alive receives
```

Never solve this with a heartbeat that re-arms `keep_alive` on a timer: a `keep_alive`
request against an unloaded model **loads** it, so a deliberate `ollama stop` is undone
within seconds.

## Traps worth knowing before touching anything

- **A prompt above the window cannot keep a prefix cache.** Every turn re-prefills the
  whole conversation. Measured: 164,383 tokens at ~157 tokens/sec, about nine minutes
  per turn, unchanged by 281 tokens of new content. Unrecoverable without compacting
  or abandoning the conversation.
- **Overflow does not error, it truncates.** A ~132k prompt was silently cut to 65,539
  tokens against a 64k window, discarding half the context.
- **Two 62 GB models do not coexist** in 128 GB. Alternating requests between them
  evicts and reloads on every turn. Check `state.py` before starting a second job.
- **`ollama create` reloads the active model**, wiping its cache. Never build variants
  while a session is live.
- **`num_ctx` in request options is ignored by the MLX runner.** Only a Modelfile
  `PARAMETER num_ctx` binds, which is why pinned per-window variants exist.
- **macOS free memory understates availability.** `state.py` reports available (free
  plus reclaimable), which is why 0.1 GB free can still be 60 GB available.
- **zsh does not word-split unquoted variables.** `for p in $PIDS; do kill $p; done`
  passes one malformed argument and kills nothing, silently. Use `${=PIDS}` or pipe
  through `xargs`. This cost two failed eviction attempts.
- **macOS bash is 3.2**: no `mapfile`, no `timeout`. A script using them fails open,
  which once evicted a model out from under a live session.

## Claude Code against Ollama

- A window applies to **new** conversations only. Resuming keeps the model the
  conversation was created with, so `--model gemma4-31b-mtp-96k` on a resumed session
  can still run the 128k variant.
- Auxiliary traffic (titling, summaries) evicts the conversation's cached prefix unless
  routed to a small model; the launcher does this.
- `scripts/claude-gemma.sh` also sets `CLAUDE_CODE_MAX_CONTEXT_TOKENS`, raises
  `API_TIMEOUT_MS` to 30 minutes so compaction of a large conversation can finish, and
  kills stale daemons holding a different model.

## More detail

- Log-line catalogue, measured rates, and the full keep-alive investigation:
  [reference.md](reference.md)
- Scripts are meant to be **executed**, not read: `state.py`, `watch.py`, `evict.sh`.
  They need only Python 3 and the standard library.
