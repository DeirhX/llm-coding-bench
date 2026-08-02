# Ollama log reference and measured behaviour

Everything here was measured on this machine: an M5 Max with 128 GB, Ollama 0.32.5,
running 26B and 31B Gemma checkpoints at 62-74 GB resident.

## Log lines that mean something

The log lives at `~/.ollama/logs/server.log`.

| Line | Meaning |
|---|---|
| `Prompt processing progress processed=N total=T` | prefill, one line per 2048 tokens. Advancing means working |
| `cache hit total=T matched=M cached=C left=L` | prefix cache reuse. `left` is what must actually be processed |
| `slot release ... n_tokens = N, truncated = 0` | healthy completion. **`truncated = 0` means nothing was cut** |
| `slot release ... truncated = 4464` | context was silently discarded. Non-zero is the alarm |
| `failed to restore ...` / `freeing all caches` | the saved prefix cache was thrown away; the next turn pays full prefill |
| `mlx runner exited unexpectedly: signal: killed` | the runner died, usually because someone killed it |
| `starting mlx runner subprocess model=...` | a load is beginning; weights follow, then `Loaded draft model` for MTP checkpoints |
| `ServeHTTP method=POST path=/v1/messages took=...` | logged **after** the response, so its absence does not mean idle |

A naive `grep truncat` reports trouble on every healthy request, because of the
`truncated = 0` line. Match `truncated\s*=\s*[1-9]` instead.

## Prefill rates, measured

| Prompt | Rate | Wall | Conditions |
|---|---|---|---|
| 3-21k | 480-650 tok/s | seconds | 62 GB model, memory to spare |
| 115k | ~480 tok/s | ~4 min | 128k window, comfortable |
| 164k | ~157 tok/s | ~9 min | above the 131k window, memory exhausted |

A prompt above the window loses both ways: it cannot keep a prefix cache, and it runs
at a third of the normal rate. Measured three consecutive turns of 164,383 tokens
differing by 281 tokens, each re-prefilled in full.

Reloading 62 GB of weights from disk cache takes about 20 seconds; the runner is
serving again roughly 7 seconds after the process starts.

## Eviction, with timings

`ollama stop` on a busy model: returns 0, changes nothing, expiry stays `-0 min`.

Killing the runner while a client retries:

```
12:37:09  mlx runner exited unexpectedly: signal: killed
12:37:10  starting mlx runner subprocess  (new pid)
12:37:17  Prompt processing progress  processed=2048  total=164383
```

One second to respawn, eight to be prefilling the same prompt again. The only order
that frees memory is: stop the clients, then stop the model, then kill the runner if it
is still resident. Verified 112 GB recovered afterwards.

Find clients by socket rather than name, since benches, probes and CLIs all count:

```bash
lsof -nP -iTCP:11434 -sTCP:ESTABLISHED | awk '/->127\.0\.0\.1:11434/ {print $2}'
```

## keep_alive: why it has to be a server default

The Anthropic-compatible endpoint ignores the field. A POST to `/v1/messages` carrying
`keep_alive: "7h"` returned 200 and left the model expiring in four minutes. `/api/chat`
honours it, which is why a launcher warm-up shows eight hours and the first real turn
knocks it back to the five-minute default.

Consequence: a 62 GB model unloads after five idle minutes, and the next turn pays a
cold load plus a full prefill.

`OLLAMA_KEEP_ALIVE` supplies the default for every request that omits the field.
GUI apps do not inherit a shell's environment, so it goes in through `launchctl setenv`
plus a LaunchAgent to survive reboot; the server reads its environment once at start, so
it must be restarted. `scripts/ollama-keepalive.sh` does all of that, and `--check`
verifies the only thing that matters: what expiry a request without `keep_alive` gets.

**Rejected approach.** A logging proxy re-armed `keep_alive` every 120 seconds. It kept
models loaded and also resurrected them: a `keep_alive` request against an unloaded
model loads it, so `ollama stop` was undone within seconds. A server default cannot do
that, because it only applies to requests that were already coming.

## Memory

`vm_stat` free pages badly understate what is available, because reclaimable file cache
is counted separately. Available is free plus inactive plus speculative plus purgeable;
0.1 GB free alongside 60 GB available is normal while a large model is resident.

Swap above roughly 8 GB precedes a throughput collapse. During the 164k prefill the
machine sat at 0 GB free with swap steady at 1.09 GB.

The size `/api/ps` reports includes the KV cache, so it grows as a conversation does:
the same 31B checkpoint read 62.3 GB when freshly loaded and 82.3 GB at 70k tokens.
Budget for the filled figure, not the loaded one, when deciding whether a second job
fits.

Two 62 GB models cannot both stay resident. When a bench and an interactive session name
different variants, every alternation evicts and reloads; symptoms are minute-long waits
in the session and a model name in `state.py` that is not the one expected.

## Context windows

`num_ctx` in request options is ignored by the MLX runner. Only `PARAMETER num_ctx` in a
Modelfile binds, hence pinned variants per window (`...-64k`, `...-96k`, `...-128k`).

The window is a cap on the cache, not a guard: exceeding it truncates silently. A ~132k
prompt against a 64k window came back processed as 65,539 tokens, half the context
discarded, no error anywhere.

## Claude Code specifics

- Resumed conversations keep the model they were created with; `--model` applies to new
  conversations only.
- Auxiliary calls (session titling, away summaries) are full requests against the main
  model unless redirected, and a large competing prefix evicts the conversation's cached
  one. Route them to a small model and disable the away summary.
- `CLAUDE_CODE_MAX_CONTEXT_TOKENS` should equal the pinned window, or the client will
  not compact before the prompt overflows it, which is the unrecoverable state above.
- Compaction of a 100k-plus conversation needs more than the default five-minute API
  timeout; at 30 minutes it completes.
- A long-lived daemon can hold a model from an earlier session. `ps -Awwo pid,command`
  and its `ANTHROPIC_MODEL` reveal the mismatch.

## Shell traps that broke real attempts

- zsh does not word-split unquoted variables: `for p in $PIDS` passes one argument.
  Use `${=PIDS}` or `xargs`. Two eviction attempts silently killed nothing.
- macOS bash is 3.2: no `mapfile`, no `timeout`, no `readarray`. A destructive script
  using `mapfile` got an empty client list, passed its own safety check, and evicted a
  model out from under a live session. Destructive tooling must fail closed.
- `dirname "$0"` does not follow symlinks. A launcher invoked through a symlink in
  `~/.local/bin` computed a repository root of `/Users/deirh/.local`, so both its system
  prompt files failed their `-f` test and a whole session ran with no prompt, while the
  banner reported `prompt: none` as though that had been asked for. Every test used the
  direct path; every real launch used the symlink. Use `${0:A:h:h}` in zsh, or walk the
  link chain by hand in bash, since macOS has no `readlink -f`. A file that was asked for
  and cannot be found should be an error, never a shrug.
- **A socket is not the only sign of a client.** Claude Code connects per request, so a
  session waiting on the user holds nothing on port 11434. Two consecutive dry runs of
  the same eviction, seconds apart, disagreed: one refused because a helper held the
  port, the next was willing to proceed, with the same session alive throughout. Client
  detection has to include processes that are merely going to come back.
