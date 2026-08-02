# Driving a local 31B as a coding agent — where the time goes, and every trap we walked into

_Compiled 2026-07-30. Companions: [`M5_MAX_128GB_VIABILITY.md`](M5_MAX_128GB_VIABILITY.md)
(**which** models fit an M5 Max 128 GB) and [`RUNNERS_MACOS_METAL.md`](RUNNERS_MACOS_METAL.md)
(**what** to run them with). This document covers **operating one for real work**: Ollama's
scheduler, prefix caches, Claude Code's undocumented behaviour, and the edit-tool failures that
eat a session._

Evidence classes: **[measured]** numbers from this machine's logs or a probe in `scripts/`;
**[binary]** read out of the Claude Code executable; **[unproven]** a live suspicion, named as
such. Every number below is one we paid for. Nothing here is inferred from documentation, because
almost none of this is documented.

## TL;DR — the ten things that cost us the most

1. **Reloading the model is cheap; losing its prefix cache is not.** Weights load in ~6 s. The
   cold prefill that follows cost 292 s where the warm one cost 77 s. In one day, 23 cold prompts
   burned **~42 minutes** of prefill that a surviving cache would have skipped. **[measured]**
2. **Only one 31B runner fits, and `-64k`/`-96k`/`-128k` are three different models to Ollama.**
   Any second client naming a different variant evicts the first. `loaded runners count=1`.
3. **The speedup is speculative decoding, not MLX.** Both checkpoints share 1245/1247 layer
   digests; the fast one carries 48 extra `draft.model.*` tensors. The MLX *runtime* is worth
   under 5 %. **[measured]**
4. **Claude Code cannot auto-compact here, and no setting will fix it.** Both code paths are
   gated on a remote feature flag that needs an Anthropic credential this machine does not have.
   `/compact` by hand is the only mechanism. **[binary]**
5. **The client's token count is not the prompt the server renders.** Measured gap: 99,005 versus
   111,186 for the same turn. The tool schemas and chat template are invisible to the client.
6. **Tool schemas cost 16,168 tokens of every single request** — one tool, `Workflow`, is 5,141 on
   its own. Withholding 16 unused tools drops framing to 4,733. **[measured]**
7. **Overflowing the window does not error, it silently degrades.** llama.cpp truncates
   (132k → 65,539 tokens, half the context gone); MLX grows the KV cache until the machine pages.
8. **A conversation larger than the window cannot hold a prefix cache at all**, so every turn
   re-prefills everything: 164,383 tokens ≈ 9 minutes per turn, three turns in a row. **[measured]**
9. **88 % of the model's edits land first try; the 12 % have three named causes**, all now
   reproducible in `scripts/edit_discipline_probe.py`: the read gutter misread as indentation,
   the identical resend, and the quote taken from a look-alike file.
10. **The Ollama server log never records which client sent a request.** Three of today's eighteen
    reloads remain permanently unexplained for exactly that reason. If attribution matters, put a
    proxy in the path *before* the incident.

## 1. Prefill and cache economics — the only cost model that matters

Decode speed is a benchmark number. In a real session, **prefill dominates and cache hits decide
everything**. From one day's `~/.ollama/logs/server.log`: **[measured]**

| Prompt | Cold | Warm | Loss |
|---|---|---|---|
| 106,710 tokens | 292 s | 77 s | 215 s |
| 108,165 tokens | 268 s | 27 s | 241 s |
| 92,456 tokens | 262 s | 6 s | 256 s |
| 164,102 tokens | 633 s | 84 s | 548 s |

Runner start to `runner is ready`: **6.1 s** (MLX maps the manifest; it does not copy 83 GB). So
when a turn takes four minutes, the weights are ~2 % of it. Ask what happened to the cache.

**What throws a cache away**, in descending order of damage:

- **A request naming a different variant.** Twelve of eighteen reloads in one day. At 13:37:41 the
  log swapped 96k out for 64k and back *inside one second* — a probe of ours against a live
  session. Two clients, two variant names, four minutes a turn for both.
- **The kernel.** `mlx runner exited unexpectedly: signal: killed` after free memory reached
  10.9 GiB while running 164k-token prompts. That is macOS jetsam, not Ollama.
- **A client hanging up mid-load.** `error loading llama server: error="context canceled"` — the
  partial load is discarded and redone from scratch.
- **`ollama create`.** Registering a Modelfile restarts the resident runner. Never build variants
  while a session is live.
- **Resuming a conversation into a cache holding a different one.** Not a reload at all, but it
  costs the same: 92,456 tokens at 1.8 % reuse, 262 s.

Of eighteen reloads, five kept the same variant. Two of those have logged causes (the aborted load
and the kernel kill above). **Three do not** — 13:38:41, 13:48:57 and 14:46:43: no error, no
variant change, and no request logged beforehand, because Ollama logs a request only on completion,
so the trigger is invisible by construction. The last two were followed by a cold ~9.3k-token
conversation that grew to 36k, twice, the passes ~370 tokens apart. That is not Claude Code (no
transcript records turns of that size in the window) and not our probe (it names the 64k variant,
which never loaded again after 13:37). **[unproven]** — a second client on the box, unidentifiable
after the fact, which is the whole argument for interposing a proxy *before* you need one.

### Keep-alive, correctly

`keep_alive` in a request body is **ignored on the Anthropic `/v1/messages` path**, so a launcher
that sets it looks right while every real turn resets the expiry to the server default. Five idle
minutes then unload 83 GB. Fix it **server-side** — `scripts/ollama-keepalive.sh` sets
`OLLAMA_KEEP_ALIVE` via `launchctl setenv` and restarts Ollama; verify with `api/ps` showing an
8-hour expiry. A client-side heartbeat is worse than nothing: ours resurrected models the user had
deliberately stopped.

## 2. Where the speed actually comes from

**Settled.** `gemma4:31b-mlx-bf16` and `31b-coding-mtp-bf16` share 1245/1247 layer digests. The
difference is 48 `draft.model.*` tensors (31.7B vs 31.3B params). One draft-equipped checkpoint,
two runners — so **all** the headline speedup is speculative decoding, and the MLX runtime is
worth **under 5 %** at matched context: ahead below 8k, behind above 64k. llama.cpp is the default
for both checkpoints. **[measured]**

**Draft advantage decays with context but never inverts** **[measured]**:

| Context | Draft speedup | Dense control |
|---|---|---|
| 400 tokens | 3.40× | baseline |
| 16k | 3.18× | −8 % |
| 34k | 2.55× | −15 % |
| 69k | 2.33× | −20 % |
| 106k | 1.93× | −24 % |

**The absolute numbers are what you budget with** — `results/speculation_acceptance_*.json`, draft on
llama.cpp, clean conditions, this machine. Everything below is decode; prefill runs at 155–350
tok/s depending on batch. **[measured]**

| Context | tok/s | A 1,400-token answer costs |
|---|---|---|
| 400 – 8k | 28 – 26 | ~55 s |
| 16k | 24 | ~60 s |
| 32k | 20 | ~70 s |
| 64k | 15.6 | ~90 s |
| 96k | 11.9 | ~120 s |
| 120k | 10.2 | ~140 s |

Two consequences worth internalising. **Decode at 120k is a third of decode at 8k**, so a long
conversation is not merely bigger, it is slower per token for the rest of its life. And **any turn
measured well below this curve is a memory problem, not a model problem** — see §4.

Two open items. Acceptance decay is real but **too small** to explain a throughput collapse from
30 to 3.8 tok/s observed once in an unattended run — memory pressure remains the live suspect.
And a *truncated* 65.5k prompt scored 1.42× where a *coherent* 69k prompt scored 2.33×, which
suggests acceptance depends on context predictability rather than length alone. **[unproven]**

## 3. Context windows, and four ways they lie to you

- **`num_ctx` in request options is ignored by the MLX runner.** Only a Modelfile
  `PARAMETER num_ctx` binds. This is why `modelfiles/` carries pinned 64k/96k/128k variants.
- **Ollama sizes an unpinned session to the model's *full* trained window** (262,144 here), not to
  a small default. Pinning is for determinism and memory, and 64k is deliberately *smaller* than
  the default, not larger.
- **llama.cpp truncates silently.** A ~132k prompt became 65,539 tokens with no error: half the
  conversation discarded, the answer confidently wrong. MLX instead grows the KV cache past its
  pinned size (62 GB resident becoming 82) and the machine pages.
- **Above the window there is no prefix cache at all.** Every turn re-prefills the entire
  conversation: 164,383 tokens at ~157 tok/s, ~9 minutes per turn, three consecutive turns.

### The framing tax nobody counts

Captured by pointing Claude Code at a fake endpoint and tokenising the real request body. **Capture
it interactively, through a pty**: a `claude -p` capture ships a different tool set (8 tools, no
plan mode, no `AskUserQuestion`) and reported 6,418 tokens where the interactive request carries
9,093 — an understatement of 30 %, and the reason the first numbers below are labelled by how they
were taken. **[measured]**

| Component | Tokens | Whose |
|---|---|---|
| Claude Code's system prompt | 2,014 | theirs |
| `AskUserQuestion` schema | 1,122 | theirs |
| Skills catalogue (injected as its own system message) | 1,094 | theirs |
| `EnterPlanMode` + `ExitPlanMode` | 1,494 | theirs |
| `Bash` 713, `Read` 466, `NotebookEdit` 435, `Skill` 426 | 2,040 | theirs |
| `Edit` 255, `WebSearch` 223, `WebFetch` 195, `Write` 169 | 842 | theirs |
| Our appended rules (skeptic + edit + formatting) | 329 | **ours** |
| Injected context reminder, billing header, identity | 158 | theirs |
| **Interactive total, 16 tools withheld** | **9,093** | |
| **Withholding 5 more (plan mode, Skill, NotebookEdit, AskUserQuestion)** | **4,477** | |

Two findings worth keeping. **Tool schemas were 60 % of the framing**, and the five extra
withholdings halve the whole tax — the largest single win being `Skill`, because dropping the tool
also drops the 1,094-token catalogue the client injects only when the tool is present. And **our own
contribution is 329 tokens, under 4 %**: the prompt rules are not the problem, whatever else is.

For all-tools mode the older figure still stands: 16,168 tokens of schemas, `Workflow` alone 5,141,
total 18,009. Also worth knowing: the client/server accounting gap cannot be measured from
`usage.input_tokens` in the transcript, because Ollama returns its *own* rendered count there and
the two match exactly. The gap is only visible between the client's status-line count and the
rendered prompt: 99,005 against **111,186**.
Declaring a window smaller than the model's (`CLAUDE_CODE_MAX_CONTEXT_TOKENS = real − reserve`)
does **not** make the client compact earlier — nothing consults a threshold, see §4 — but it makes
the status-line percentage honest, which is the only trigger that works here.

**The reserve must exceed the framing, or it is worse than no reserve at all**, because it hands the
client a ceiling whose own arithmetic overflows the runner. An audit on 30 Jul caught this: the
full-tools reserve was 16,384 against framing of 18,009, so a client obeying its 81,920 ceiling
still rendered 99,929 into a 98,304 window. Now 8,192 lean (declared 90,112 → 94,845 rendered,
3,459 slack) and 20,480 full (declared 77,824 → 95,833 rendered, 2,471 slack). Re-derive both if
the tool set changes.

### What actually fills the window: whole-file reads, not boilerplate

Measured on a live session at 83,532 rendered tokens, counting the conversation since the last
compaction. **[measured]**

| Category | Tokens | Share |
|---|---|---|
| Tool results | 67,674 | **82.1 %** |
| Reasoning (thinking blocks, echoed back every turn) | 5,828 | 7.1 % |
| Tool call arguments | 3,619 | 4.4 % |
| What the user typed | 3,148 | 3.8 % |
| Compaction summary | 1,511 | 1.8 % |
| What the model said in prose (one block) | 618 | 0.7 % |
| Framing, on top of all of it | 9,093 | — |

Sums to 91,491 against 96,295 rendered; the ~4,800 residual is per-message chat template markers and
chars/4 error. **My first pass at this table was wrong** and said 90.9 % for tool results: it built
its bucket key from a field absent on some records, so it missed all 33 reasoning blocks and half the
user's own messages. Corrected above. Reasoning being echoed back is worth knowing — 5,828 tokens of
it here, paid on every subsequent turn, and independent of whether the UI displays it.

The ten largest results were **all whole-file `Read` calls** and accounted for 65,933 tokens, 80 % of
the conversation. `benches/pyhard/bench.py` appeared **twice** at ~11,940 each, `benches/repohard/
bench.py` twice at ~5,200. So framing was 9 % of the prompt and re-reading two files was 37 %.

The uncomfortable part: the edit-discipline rule *caused* some of this, by telling the model to read
a file again after each change without saying how much of it. Amended 30 Jul to require a narrow
read-back with offset and limit, and to prefer a search command over reading files whole. Not yet
measured. When auditing context growth, measure composition **before** trimming boilerplate: a
9,093-token framing looks damning until you notice one duplicated file read cost two and a half
times as much.

### A single task can fill the window, and nothing will compact it: use a hook

Since auto-compaction cannot fire (§4) and a task cannot be compacted while it runs, the only
workable strategy is to **make the task stop before the window fills, and slow the rate it fills
at**. Prompt rules are not enough — the read-discipline rule was in force during the session that
read one file twice at 11,940 tokens. `scripts/cc-context-guard.py` is a `PreToolUse` hook doing the
same job arithmetically. **[measured]**

What the client permits, verified by probing rather than from documentation:

| Fact | Evidence |
|---|---|
| Hooks register from a `--settings` blob, not only global settings | hook fired, payload on disk |
| Payload carries `transcript_path`, `cwd`, `session_id`, `permission_mode`, `tool_name`, `tool_input`, `tool_use_id`, `effort`, `prompt_id` | recorded |
| `permissionDecision: "deny"` is honoured **under `bypassPermissions`** | `permission_mode` was `bypassPermissions` and the call was refused |
| `permissionDecisionReason` arrives as the `tool_result`, so the model reads it | text recovered from the next request body |
| `updatedInput` can rewrite a call instead of refusing it | schema: `{allow, updatedInput?}` / `{deny, message}` |

The guard denies three things: an unbounded `Read` of a file over 500 lines, quoting its real line
count; a re-read of a file unchanged since it was last read, quoting when that was; and, past 80 %
of the runner's window, anything bulky — with an instruction to write findings to `NOTES.md` and
stop. `Write` and `Edit` stay allowed at all times, or the handoff would be impossible. It fails
open on every unexpected condition, and `touch /tmp/cc-guard-off` lifts it without a restart.

Three traps found while building it, all worth remembering:

- **A non-streamed `tool_use` reply makes the client silently re-issue the whole turn.** Plain text
  is accepted un-streamed; a tool call is not. Nothing is logged — it looks exactly like a model that
  ignores its tools. Fake endpoints must speak SSE.
- **Transcript timestamps are UTC.** Parsing them with `time.mktime` places every event an hour or
  two early and silently disables any comparison against file mtimes. Use `calendar.timegm`. My own
  selftest caught this; without the test the duplicate-read check would have been decorative.
- **The `Read` parameter is `file_path`, not `path`**, and an invalid tool input is rejected before
  hooks run.

**Three bugs in the first version, all found by auditing rather than by using it** — worth listing
because each would have been invisible until it wasted a session:

1. **The refusal poisoned the file.** A refused call is written to the transcript as an ordinary
   `Read` tool_use; only the *result* carries `is_error`. So the duplicate check counted the refusal
   as a read and refused the narrower retry it had just demanded. The model would have looped, one
   turn per attempt, looking like a model too stupid to follow an instruction.
2. **An oversized limit walked straight past it.** The check was for a *missing* `limit`, so
   `offset 1, limit 5000` read the file whole — which is exactly what a model does when told to use
   a limit. A limit above the cap now counts as unbounded.
3. **The stop threshold refused `git commit`.** The refusal tells the model to record its findings,
   then blocked it from landing them. `git add`, `git commit`, `git status --short` and
   `git diff --stat` are now exempt; `pytest` and the rest stay refused.

`scripts/cc_context_guard_test.py` covers all of it — 23 cases, including every case that must
*allow*. Replaying the guard over the reads that actually happened in two real sessions: it would
have refused 29 of 39 reads and kept up to 190,989 of 204,969 read tokens out of the context. Read
"up to" strictly — a refused read is retried narrowly, so the retry still costs something, and the
duplicate count there ignores mtime, so the true figure is lower. The trade is roughly 29 extra
round trips against two or three compactions avoided.

Not measured: whether the model handles a refusal gracefully or argues with it. The refusal text is
written to be actionable, but no live session has run under the guard yet.

## 4. Claude Code, as it actually behaves against a local endpoint

All of this was read out of the CLI binary (v2.1.218) and confirmed against live sessions.
**[binary]** + **[measured]**

### Auto-compaction cannot fire, and cannot be made to

Two independent paths, both closed:

- **Threshold path** needs `Je("tengu_sepia_moth")`, a remote feature gate that **defaults to
  false and is never fetched**, because gate fetching requires an Anthropic credential this
  machine does not have — no keychain entry, no credentials file. Removing
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` left `cachedGrowthBookFeatures` still absent, so the
  flag is not the cause. The launcher's `ANTHROPIC_AUTH_TOKEN=ollama` satisfies local checks only.
  (For reference, the reconstructed threshold for a 98,304 window and 8,192 max output would have
  been ~77,112 tokens.)
- **Reactive path** fires only when the API returns HTTP 400 "prompt is too long". **Ollama never
  says that** — it grows the KV cache instead. Driving it deliberately from a fake endpoint
  surfaced three further preconditions: `QI()`/`autoCompactEnabled` must be true (it is false
  here), `ktd` refuses when `Ttn(e).length < 2` (fewer than two message groups), the error must
  carry token counts in `errorDetails` or the message degrades to the bare "Prompt is too long"
  constant, and a rapid-refill breaker trips after three quick refills.

### What a compaction actually costs, and how to make it cheaper

All three on record were **manual**, and they cost 311 s, 528 s and 666 s. **The summary is not the
expensive part**: measured from the `isCompactSummary` records, the summaries were 4,286 / 5,362 /
5,715 characters — roughly **1,072 / 1,340 / 1,429 tokens**. At the clean rate for that context
(§2, ~10–12 tok/s at 96–120k) a 1,400-token summary should take **~140 s**. **[measured]**

| When | Prompt | Cache reuse | Summary | Wall | Implied decode |
|---|---|---|---|---|---|
| 30 Jul 14:19 | 114,146 | 85.5 % (16.5k fresh ≈ 70 s) | 1,072 tok | 311 s | ~4.4 tok/s |
| 30 Jul 15:36 | 92,456 ×2 | 1.8 % then 99.7 % | 1,340 tok | 528 s | ~5.1 tok/s + a full cold pass |
| 29 Jul 17:48 | 109,754 | — | 1,429 tok | 666 s | ~2.1 tok/s |

So decode ran **2–5× below the measured curve**. The leading explanation is that the compaction
request is the largest prompt of the session, and it exceeds the runner's pinned window: the 31B
sits at 87.2 GB with `ctx=98304`, and prompts of 111k–114k made the MLX runner grow its KV cache
until `peak memory` read 100.8 and 107.9 GiB against ~107.5 GiB of Metal budget, with 77 GB wired
and 6.3 GB of swap in use. That is paging, and paging is what 2 tok/s looks like. **[unproven]** in
the strict sense — no controlled experiment yet; the test is to compact once at ~70k and time it.

**Levers, by measured payoff:**

1. **Compact while the rendered prompt still fits inside the pinned window.** Below 98,304 the KV
   cache stays in its allocation and decode should return to ~12 tok/s: 311 s becomes ~140 s. In
   practice: act when the status line asks at **60 %** (≈54k client tokens, ≈59k rendered), which
   is where the 187 s compaction happened. `scripts/cc-statusline.py` goes bold red at 75 %, and
   that is already late rather than early.
2. **Compact at 64k rather than 96k for another ~30 %** (15.6 vs 11.9 tok/s), and at 32k for 20.
   The curve in §2 is measured, so this is arithmetic rather than hope.
3. **Compact straight after a turn — never after a large read, and never after a resume.** The
   fresh-prefill share is exactly what `cache hit matched=` reports: 85.5 % reuse cost 70 s, while
   a resumed session paid a **full cold pass of 262 s** on top of the generation. Compact before
   quitting, not after restarting.
4. **`/clear` plus a handoff file beats `/compact`** when you do not need the verbatim history:
   ask for a 600–1,000-token state note (~60–80 s if written at 70k context), `/clear` costs
   nothing, and the next session reads the file in seconds. Roughly 5× cheaper than a compaction,
   and the artifact is inspectable, editable and survives across days.
5. **`/compact <instructions>` is supported** — `customInstructions` is plumbed into the summariser
   path **[binary]** — but it is a *content* lever, not a speed one: the prompt asks for a
   "detailed summary" and still produced only ~1,400 tokens, so halving it saves ~40 s. Use it to
   preserve open items and drop narrative, not to go faster.

**Rejected, with reasons:** routing the summariser to the 1B (trained window 32,768, so it cannot
hold the conversation at all); pinning 128k to avoid the overflow (clean decode at 120k is still
10.2 tok/s, and the 128k runs are the ones the kernel killed); evicting the small model to free
memory (it is 1.0 GB resident, not a factor); lowering `CLAUDE_CODE_MAX_OUTPUT_TOKENS` (truncates
the summary mid-sentence instead of shortening it).

The UI will not remind you to do any of this, so `scripts/cc-statusline.py` does.

### The environment variables that matter

| Variable | Why |
|---|---|
| `CLAUDE_CODE_MAX_CONTEXT_TOKENS` | Denominator for the status line; declare real window minus framing reserve |
| `CLAUDE_CODE_MAX_OUTPUT_TOKENS` | 8,192 here. Max observed real output: 3,409, no `max_tokens` stops — it is a runaway guard, not a limit |
| `API_TIMEOUT_MS` | 30 min. The 5-minute default abandons any turn that needs a cold 100k prefill, then retries it |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` / `ANTHROPIC_SMALL_FAST_MODEL` | Route auxiliary calls to a 1B, or titling requests land on the 83 GB model |
| `CLAUDE_CODE_ENABLE_AWAY_SUMMARY=0` | The away summary displaces the conversation's cache on return from idle |
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | Stops telemetry. Does **not** cause the compaction problem (verified by removing it) |

Three traps around them:

- **`~/.claude/settings.json` overrides your per-session settings**, and its `env` block leaks to
  every spawned worker. We chased "cache thrashing" for hours before finding stale `ANTHROPIC_*`
  entries there. Remove them, and `export` from the launcher so children inherit.
- **`--append-system-prompt` and `--append-system-prompt-file` are mutually exclusive**, so
  multiple rules must be composed into one temporary file.
- **The status line receives** `context_window` (`total_input_tokens`, `context_window_size`,
  `used_percentage`), `model` and `effort` on stdin as JSON — the client's own numbers, not an
  estimate. That is the cheapest honest instrument available.

### Sessions, daemons and the resume trap

- **`--model` applies to new conversations only.** Resuming keeps whatever model the conversation
  was created with, whatever the launcher banner says. We spent real time confused by a "96k"
  session that was demonstrably serving 128k requests.
- **A long-lived daemon keeps its original environment** and will hold a stale model resident.
  The launcher now kills mismatched daemons when no other session is live, and refuses to launch
  when one is.
- **Concurrent sessions share one runner.** Count distinct `--session-id` values, not processes,
  or every helper looks like a rival session.

### Formatting

The model will emit LaTeX (`task $\rightarrow$ id`) in a terminal that cannot render it. A rule
scoped to "arithmetic, formulas and units" does not cover an arrow — the model was technically
compliant. Scope the rule to **any markup a terminal cannot render, symbols in prose included**.

## 5. The edit tool: a measured failure taxonomy

Across recent sessions, **88 % of edits succeed first try**. The failures are not random:
**[measured]**

| Fault | What happens | Evidence |
|---|---|---|
| **Gutter-as-indentation** | The read renders `54\t2. Add ...`; the model quotes it with four invented leading spaces | Three README failures in one session, then success with a 9-character anchor |
| **Identical resend** | After a rejection, the same `old_string` is sent byte-for-byte again | 2 of 9 failures across two sessions |
| **Wrong file** | Correct text, wrong target — the same function exists in two files, and a misdirected edit is **accepted silently** | Three failures quoting a line that existed only inside a fixture string in our own probe, read 5 minutes earlier |
| **Stale view** | Editing against a pre-change read of a file it already modified | Recurrent; re-read after each edit |
| **Oversized quote** | 194-line exact matches, brittle to one character | Suspected, then **exonerated**: 42 probe runs, 47 edits, 0 failures in both arms, including six byte-perfect 82-line quotes |

Two lessons about *measuring* this. First, quote size was our leading hypothesis and it was wrong;
the probe that disproved it took an afternoon and was worth it. Second, **a control arm that scores
100 % proves nothing about a rule** — at short context neither arm ever failed, so the rule could
not show a benefit. Faults must be reproduced before a mitigation can be evaluated;
`prompts/edit_discipline.md` (143 words) now names the gutter, the resend and the cross-file quote,
and `scripts/edit_discipline_probe.py` has 9 tasks including `markdown_list` and `wrong_file`,
which reproduce the first and third exactly, with Claude Code's own error strings.

## 6. Diagnostics — what to run, and what the logs cannot tell you

The `ollama-watch` skill carries the working procedure. The essentials:

- **`api/ps` expiry is not a busy signal.** A runner can decode flat-out with eight hours of
  keep-alive left. Use CPU (>40 %) to judge busy.
- **`vm_stat` "Pages free" understates badly** on macOS; report free + inactive + speculative +
  purgeable. But *availability* is the wrong worry: watch **wired** (unswappable GPU/model
  allocations, 77 GB observed) and **swap used against swap total**, since the reclaimable file
  cache makes everything look comfortable while the machine pages.
- **Read the log's own vocabulary**: `cache hit total=/matched=` is the only honest statement of
  what a turn will cost; `peak memory` distinguishes models (31B > 60 GiB, 1B nowhere near);
  `ServeHTTP ... path=/v1/completions` is the *runner's* internal endpoint, so it appears
  regardless of which client-facing API was used.
- **Requests are logged on completion, not arrival.** The request that triggered a reload appears
  *after* the restart, which makes causation genuinely hard to read.
- **There is no client identity anywhere in the log.** To attribute traffic you must interpose:
  move Ollama to another port and run the logging proxy on 11434. Doing this after the fact is
  impossible, which is how two reloads today ended up permanently unexplained.
- **Evicting safely is harder than it looks.** `scripts/evict.sh` must detect clients that are
  *idle between turns*, not just holding a socket, or it kills a runner a live session is about
  to reuse. Two dry runs seconds apart disagreed until it also matched Claude Code processes by
  command line. It fails closed if `lsof` is unavailable.

## 7. Benchmarking lessons that transfer

- **`BENCH_REALISM`** disables every harness rescue (think-loop detection, nudge-and-retry,
  think-to-answer promotion) in one switch. Any score gathered with rescues on describes the
  harness, not the model.
- **`BENCH_TEMPERATURE=auto` omits the key entirely**, so the model's own Modelfile sampler is
  what gets measured. Otherwise you measure the bench's defaults.
- **Ship fixes in the Modelfile, not the harness.** Clients that send no options (Claude Code
  among them) inherit whatever the Modelfile pins: temperature 0.1, `num_predict`, repeat penalty,
  stop sequences. Ollama's unbounded `-1` default is otherwise a hung request.
- **The text protocol was the bug, not the model.** `repohard`'s fabrication pathology vanished
  under native tool calling: 8/8 arms clean, zero fabrication. Real markers are
  `<|tool_response>response:Name{value:...}<tool_response|>`; the `<arch_result>` stop we shipped
  is bench-only and can never fire in an agent CLI.
- **A 63-word generic system prompt (`prompts/skeptic_min.md`) took the 31B from 0/20 to 20/20 on
  false-bug traps at zero cost to its fix rate.** It does not transfer to the 26B, where every
  prompt tried cost fix points. Prompt fixes are model-specific.
- **With rescues off**: 31B scores 74/80 tuned and 70/80 shipped with zero unclean endings; the
  26B 60/80 tuned versus 23/80 shipped (one truncation, 21 fabrications). Tuning matters most
  where the model is weakest.
- **Resume logic needs a key that matches the data.** `bench_lib/bench_runner.py` defaulted
  `id_attr` to `"id"` while every result file keys tasks as `"task"`, so the merge silently
  matched nothing and re-ran completed work. It was also dead code, which is why nobody noticed.

## 8. How we were blind — hypotheses that were wrong, and what killed them

Kept deliberately, because the wrong turns cost more than the right ones.

| We believed | Actually | Killed by |
|---|---|---|
| MLX gives a ~3.5× runtime speedup | Shared weights; the speedup is a draft model. Runtime worth <5 % | Comparing layer digests |
| Slow turns were memory pressure | Partly cache restores, partly variant thrashing; memory peaks did not predict failures | Correlating peaks against stalls |
| Auxiliary calls were evicting the cache | The real cause was stale `ANTHROPIC_*` in `~/.claude/settings.json` reaching every worker | A logging proxy naming the caller |
| A smaller declared window would force compaction | Nothing consults a threshold; the reserve only fixes the status-line denominator | Reading the binary's gate logic |
| Long quotes caused the edit failures | 47 edits, 0 failures, including 82-line quotes | The probe, in both arms |
| A 5-minute unload was a keep-alive misconfiguration | The messages path ignores body `keep_alive` entirely | Watching the expiry reset each turn |

**Tooling traps that silently corrupted work**, all of which produced *plausible* wrong results:

- `dirname "$0"` does not resolve symlinks. Launched through its symlink, the launcher set
  `ROOT=/Users/deirh/.local`, both prompt files failed their `-f` test, and every real session ran
  with **no system prompt** while the banner said so in a line nobody read. A missing prompt is
  now a hard error. Use `${0:A:h:h}` in zsh.
- The **edit tool strips leading whitespace** from hand-typed multi-line parameters (see
  `AGENTS.md`): 4 spaces arrive as 0, nested blocks compound. Write Python via heredoc.
- **bash 3.2 on macOS has no `mapfile`** — the client list came back empty and the script killed
  a live runner.
- **zsh does not word-split unquoted variables**, so `kill -TERM $PIDS` passes one malformed
  argument and fails silently. Use `${=PIDS}` or `print -l | xargs -n1`.
- **`bash -n` cannot validate a `#!/bin/zsh` script.** Different grammar; use `zsh -n`.
- **Python buffers stdout in a detached `screen`**, so a working monitor looks dead. `flush=True`
  or `python -u`.
- **Regexes that match the negative case**: a "trouble" pattern matched `truncated=0`, which means
  *not* truncated. Anchor to `truncated=[1-9]`.
- **Transcripts are UTC; the Ollama log is local.** A two-hour offset made session starts look
  unrelated to reloads they had caused. Convert before correlating.

## 9. Still open

- No long session in a large repo has been run on either backend; verification was one
  read-and-answer task per model.
- The 30 → 3.8 tok/s MLX collapse has no established cause. Acceptance decay is insufficient.
- `claim` and `arch` ran 1.7× and 1.3× *slower* on MLX at identical scores, unexplained.
- Whether `skeptic_min.md` survives Claude Code's own system prompt is **still unmeasured** — the
  symlink bug means no interactive evidence exists at all.
- The sharpened edit rule (gutter, no-resend, cross-file) is unmeasured; the probe tasks exist and
  need both arms once the GPU is free.
- The 26B solves half of `repohard` deterministically and coin-flips the rest (range 20 over 5
  runs). Unquantified in a long session.
- Whether acceptance depends on context *coherence* rather than length. Untested directly.
