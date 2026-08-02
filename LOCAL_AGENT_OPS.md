# Driving a local 31B as a coding agent — where the time goes, and every trap we walked into

_Compiled 2026-07-30, §8 added 2026-08-01. Companions: [`M5_MAX_128GB_VIABILITY.md`](M5_MAX_128GB_VIABILITY.md)
(**which** models fit an M5 Max 128 GB) and [`RUNNERS_MACOS_METAL.md`](RUNNERS_MACOS_METAL.md)
(**what** to run them with). This document covers **operating one for real work**: Ollama's
scheduler, prefix caches, Claude Code's undocumented behaviour, and the edit-tool failures that
eat a session. What we intend to **build** on top of these constraints is
[`DEPTH_PIPELINE_PLAN.md`](DEPTH_PIPELINE_PLAN.md); this file is the evidence it cites._

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
- **Loading any second model at its *default* context.** `ollama pull qwen3:4b` is harmless; the
  first request to it is not. It loaded at the model's declared 262,144-token context — **42 GB** —
  and the scheduler evicted a resident 92 GB 31B to fit it. A 2.6 GB download bought a full
  re-prefill for the live session. Pass `options.num_ctx` on the first call to any model you are
  only probing with.
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

## 8. Gating the model, and parking its context — what the mechanisms actually do

Probed 2026-08-01 before building anything on them. Five assumptions we were about to build on
turned out to be wrong, one of them the load-bearing one. Every probe is in `scripts/phase0/`, so
the numbers below can be re-run rather than believed. **[measured]**

### The Stop hook is a usable gate

A `Stop` hook that returns `{"decision": "block", "reason": ...}` does everything a depth gate
needs. Verified end to end against `claude -p --output-format json`, which reports `num_turns` and
so prices each block exactly:

| Fact | Evidence |
|---|---|
| A block is honoured and the model answers again | final result was the token supplied only in the refusal reason |
| The reason reaches the model verbatim | canary `GATE-7731-OK` came back as the whole answer |
| A block costs **exactly one extra turn** | `num_turns` 1 → 2 for one block, 1 → 4 for three |
| `stop_hook_active` is true on re-entry | loop protection is the hook's to honour, not the client's |
| **Three consecutive blocks are honoured** | no client-side cap at three; the gate can demand rounds |
| The payload carries `last_assistant_message` | the gate reads the answer without parsing the transcript |

`UserPromptSubmit` → `hookSpecificOutput.additionalContext` also reaches the model intact, which is
how a task contract gets injected without editing the system prompt.

### `PostToolUse` does not fire when a tool fails

Two for two: a `wc` with a bad argument and an `ls` of a missing path both ran, both landed in the
transcript with `is_error: true`, and **neither produced a hook event**. An evidence recorder built
on `PostToolUse` therefore loses precisely the failures worth recording, and looks like it is
working while it does so.

Use the transcript instead. It is a strict superset: `toolUseResult` carries the same structured
object for successes — for `Read`, `{filePath, content, numLines, startLine, totalLines}`, i.e. the
exact bytes the model saw — and the error text for failures. The `Stop` payload hands you
`transcript_path`, so the gate can read ground truth at the moment it needs it, and the per-tool
hook process can be dropped entirely.

### Probing the client needs a tool-capable model, and `gemma3:1b` is not one

`gemma3:1b` has no `.Tools` in its template, so Claude Code cannot drive it at all:
`400 ... does not support tools`, one turn, zero output. `llama3.2:3b` accepts the schema and then
fabricates file contents rather than calling `Read` — useless as a harness, though an unintentional
demonstration of the failure mode we are building against. `qwen3:4b` (2.6 GB) calls tools
correctly and is the cheap stand-in for client-mechanism work. Prefix a probe prompt with a slash
and the client eats it as a command: put `/no_think` at the end.

### A parked context survives an intervening request — until it does not

The MLX runner's prefix cache is not single-entry. Sending A, then a completely different B, then A
again returned `cache hit total=2371 matched=2371` with **0 ms** of prefill on the third request.
So the pipeline may hand off between stages without automatically paying to rebuild the first one.

It is bounded, and the failure is total rather than partial:

| Step | Prompt | Result |
|---|---|---|
| A cold | 54,629 tok | `matched=11`, 128 s |
| B cold | 57,229 tok | `matched=13`, 149 s |
| A again | 54,629 tok | **`matched=13`**, 140 s |

### What actually bounds retention: 8 GiB, not the window

The obvious reading of the table above — two ~55k contexts do not fit a 98,304-token window — was
written into this document as "the sum of contexts you keep must stay under the runner's window."
**That was wrong**, and the retention probes killed it before anything was built on it.

Fourteen distinct 9,337-token contexts were created back to back, 130,718 tokens in total, while
`ollama ps` never moved off 72 GB: the pool does not grow with what it caches, so it is not the
window being filled. Probing newest-first (`retention3.py`, 12 × 9,338) found **two** survivors,
18,676 tokens. Repeating at a finer grain (`retention4.py`, 14 × 2,510) found **three**, 7,530
tokens. Neither a fixed token count nor a fixed entry count survives both numbers, so the black-box
approach was abandoned for the source.

`x/mlxrunner/prefix_cache.go` in v0.32.5 settles it in one line:

```go
const maxPagedOutBytes int64 = 8 << 30 // 8 GiB eviction threshold for paged-out snapshot memory
```

The cache is a compressed prefix trie in which **exactly one path is live** at a time; every other
retained context is a per-layer snapshot paged out of the live arrays. `enforceEvictionPolicy` runs
whenever a path is switched or a snapshot is attached, and evicts until the *bytes* of paged-out
snapshots fall under 8 GiB. So the ceiling is a hard-coded byte budget with no relation to `num_ctx`
— raising or lowering the context window will not move it.

**Retention is not a token count at all, which is why no single token figure fitted.** A snapshot is
taken per layer, and gemma4's 60 layers are not alike: `config.json` gives 10 `full_attention` and 50
`sliding_attention` with a 1024-token window, 16 KV heads and `head_dim` 256 in bf16, so one layer
costs 2 × 16 × 256 × 2 = **16 KiB per token**. The two cache types snapshot different things
(`x/mlxrunner/cache/`): `KVCache.Snapshot(from)` keeps `[from, offset)`, the node's whole edge, while
`RotatingKVCache.Snapshot(o)` keeps `min(o, maxSize)` — the window, and nothing else, however long
the edge. So one paged-out node costs

    50 × min(offset, 1024) × 16 KiB   +   10 × edge_tokens × 16 KiB
    = up to 800 MiB, flat, per node   +   160 KiB per token of that node's edge

and the 8 GiB budget buys **either ~9 parked nodes anywhere past the first 1024 tokens, or one node
of ~47k tokens, or any mix**. The flat term dominates ordinary use, and it is per *node*, not per
conversation: a session that pages out once per turn spends 800 MiB a turn on windows it will
restore in a millisecond. That is what made 12 × 9,338 and 14 × 2,510 disagree — the probes differed
in node count far more than in tokens, and tokens were never the variable.

The alternative reading — that snapshots ignore the window and keep all 60 layers full length, at
960 KiB per token — is ruled out by a measurement already in this document: the fan-out probe matched
**16,387 tokens** from a paged-out node of an earlier session. At 960 KiB/token that node alone would
have been 15 GiB and could not have survived a 8 GiB budget; at 160 KiB/token it is 3.3 GiB, and it
did.

`scripts/phase0/kv_budget.py` computes this from any model's own config, and reading it against the
two probes is the honest test. It predicts 6 survivors at 2,510 tokens each and 3 at 9,338; the
probes saw **3 and 2**. Same order, consistently optimistic, and the direction is the expected one —
each probe attaches snapshots of its own, so it evicts against a budget it is itself consuming. Take
the arithmetic as an upper bound and halve it when planning. What would settle the remaining factor
is the runner's `paged_out:` trace line, which needs a restart to enable and has not been worth one.

Two clauses in the eviction walk matter more to the design than the budget does:

```go
if n == c.root || activeSet[n] || len(n.children) > 1 { return true }  // never evicted
// otherwise: oldest lastUsed, then deepest, then largest
```

**A node with more than one child cannot be evicted at all** — `evictNode` panics if asked. The
moment a shared head has two divergent continuations hanging off it, it is pinned for the life of
the runner, and every later sibling restores it for free no matter how much traffic runs in
between. That is not a happy accident of the fan-out measurements below; it is the policy. The
corollary is the sharp edge: eviction only ever reclaims the *leaves*, so what you must budget is
not the sum of your contexts but **the sum of the per-agent tails hanging off the shared head**,
against 8 GiB — where each tail costs its own 800 MiB of windows before its first divergent token.

`num_ctx` still bounds a single conversation — one agent at 96k is at the window and gets the
context-shift treatment past it — but it never bounds the sum across agents.

The revised rule to design to: **share a byte-identical head across agents, keep the divergent tail
of each one small, and stop counting against 96k.**

The same run prices prefill at scale: **~700 tok/s at 2.4k tokens, ~428 tok/s at 55k**. Prefill cost
grows worse than linearly with context, so splitting one 100k stage into two 50k stages is cheaper
than the token count suggests.

### What a subagent costs, and what it can be held to

Measured on the resident 31B, since the question is about this model's cache, not a stand-in. One
`claude -p` session delegating a single read to `subagent_type: general-purpose`, all tools enabled.
The request sequence, read from `prefix_cache.go` lines in order:

| # | Who | Prompt | Matched | Note |
|---|---|---|---|---|
| 1 | parent turn 1 | 17,871 | 16,387 | warm from an **earlier session** — identical framing |
| 2 | parent turn 2 | 18,073 | 17,970 | |
| 3 | **subagent turn 1** | 9,740 | **27** | `cache miss`, fully cold, 18 s |
| 4 | subagent turn 2 | 10,002 | 9,817 | |
| 5 | parent resumes | 18,311 | 18,103 | **the parent's context survived** |

Four things fall out of that, and only one of them was expected.

**A subagent inherits nothing from its parent.** 27 tokens of 9,740. Claude Code gives a subagent
its own system prompt, and a prefix cache matches from the first token, so a differing head means no
reuse at all. Every delegation is a cold prefill of the subagent's whole framing — 18 s here, and it
scales with whatever context you hand it. Whether *sibling* subagents can share their common head
with each other is a separate question with a more interesting answer, below.

**Delegation does not destroy the parent**, as long as both fit. 18k plus 10k left the parent
matching 18,103 of 18,311 on resume. What "fit" means is the 8 GiB snapshot budget, not the window:
the parent is a paged-out leaf while the subagent runs, and it comes back intact only while nothing
has pushed the trie's snapshot bytes past the threshold. A parent at 50k delegating to a subagent
at 40k is well past it, and the parent is then discarded whole and costs its full prefill back,
which was 140 s at 55k.

**A subagent is cheaper per turn than its parent**: 9,740 tokens of framing against 17,871, because
it is given a smaller tool set. Delegating a narrow job to a narrow agent genuinely costs less per
turn than doing it in the main session — the cold prefill is the entry fee.

**A new session starts warm.** Request 1 matched 16,387 tokens from a *previous, unrelated* session,
because the system prompt and tool schemas were byte-identical. A pipeline of disposable
single-purpose sessions therefore does not pay for framing on every stage, provided nothing
perturbs the head of the prompt. That is the strongest argument yet for the byte-identical prompt
head the contract injection depends on.

**Where the subagent's evidence lives.** Not in the parent transcript: it holds only the `Agent`
call and a `toolUseResult` summary (`agentId`, `agentType`, `totalTokens`, `totalDurationMs`,
`toolStats`, and the final text). The work itself is in a separate file,
`<project>/<parent-session-id>/subagents/agent-<agentId>.jsonl`, and every hook fires with the
*parent's* `session_id` and `transcript_path`, so nothing in the payload points at it. Worse, that
file carries **no `toolUseResult` records** — the structured `{content, startLine, totalLines}`
object exists only in the parent transcript. What survives is the rendered `tool_result` text with
the `N\t` line gutter, which is verifiable but is also exactly the gutter the model misreads as
indentation (§5). An auditor of subagent work must parse that path by hand.

Two incidental costs worth budgeting for: the model first invoked `subagent_type: "worker"`, which
does not exist here (`claude`, `Explore`, `general-purpose`, `Plan`, `statusline-setup` do), and
then omitted the required `description` field. Each mistake burned a turn — 102 s for the first
attempt — and **neither failed call produced a `PostToolUse` event**, a third confirmation of that
gap.

### The shared prefix *is* reusable — but the first branch pays, and fan-out wipes the cache

"A subagent inherits nothing" is true of the parent's prefix and false as a general statement, which
matters because every subagent of a given type is issued the same system prompt. The runner clearly
*sees* the sharing: it reports `matched=8647` for a second subagent's 9,745-token prompt. It simply
does not always use it — `cached=27`. **`matched` is what the token list agrees on; `cached` is what
was actually restored into the KV, and `left` is what gets prefilled.** Reading `matched` as reuse
will make a cold turn look warm.

What decides it, measured at ~3k tokens with a nonce so nothing was pre-cached (`cache_split.py`):

| Request | matched | cached | Wall |
|---|---|---|---|
| A, first use of the prefix | 14 | 14 | 5.39 s |
| B, **first branch** off it | 3,027 | 14 | 4.65 s |
| C, second branch | 3,027 | **3,027** | **0.39 s** |
| D, third branch | 3,027 | **3,027** | **0.35 s** |

So a shared prefix becomes reusable only once a *second* conversation has diverged from it — the
first divergence pays full prefill and appears to be what creates the shared node. Two further
conditions came out of the same series: the source context must not be the one the runner just
served (branching off the active context loses, `cache_branch.py`), and a single branch never
suffices no matter how small the divergence — 32, 128, 512, 1,024, 2,048 and 4,096 tokens of tail
all lost identically (`cache_threshold.py`), which is what killed the more obvious "the divergence
must be shallow" theory.

The clean implication would be that subagent three onward is free. **It is not, and the reason is a
bug.** Running two and then three subagents in one session, the third still restored 27 tokens,
because immediately beforehand the runner logged:

```
WARN source=prefix_cache.go:255 msg="failed to restore cache, freeing all caches" offset=17907
```

The offset is the parent's cached size in both runs (17,900 and 17,907). The first parent resume
after a subagent restores correctly; a later one fails and takes **every** cache with it, so the
parent then re-prefills 18,569 tokens from scratch and any shared subagent node is gone. One
subagent never triggered it; two and three did, every time. Against 689 cache events in the log,
this warning has fired three times in total and two of them are those runs. **[measured]**, n=2, but
the correlation is not subtle.

**What it is not: aborts, and not concurrency.** Both suspects were tested directly
(`wipe_cause.py`). Cutting the client connection four seconds into a fifteen-second prefill — the
abandoned-prefill case the source comments warn about — left the earlier context restoring at 0.39 s
with no warning logged. Firing two full-size requests simultaneously at the `-np 1` runner merely
serialized them (15 s and 30 s) and likewise left the earlier context warm. The ops note that
concurrency was "the obvious difference to chase" was wrong.

**What it correlates with**, reading the two wiped sessions back: the wipe follows a *second sibling
splitting an existing leaf mid-edge* (`matched=8647 cached=27`), and only where the parent had
itself returned on a **partial** restore — `matched=18103 cached=17900`, the cached figure short of
the matched one, which is precisely the misalignment the failing code path complains about. Both
sessions show that sequence identically. Synthetic splits alone do not reproduce it, so something
about the real pattern — most likely the long generations CC attaches to the trie between splits —
is still missing. **[unproven]**

**Priced, it is a tax rather than a blocker.** Summing `left=` against `total` across the two wiped
sessions, Claude Code with real subagents still avoided **63 % and 37 %** of its prefill, spending
138 s and 171 s of prefill respectively. The wipe fired once per session, not repeatedly. So the
budget for in-client fan-out is: every subagent pays a cold prefill of its own framing (9,740
tokens, ~18 s), the parent pays one full re-prefill somewhere around the second delegation (30 s at
18k, minutes at 60k), and roughly half the prefill is still saved. The scripted fan-out saves nearly
all of it; the difference is the price of staying inside the client.
Priming the shared prefix deliberately — two throwaway requests with the same head and different
tails — is only worth doing on a runner that does not then throw the result away.

### A fan-out that the cache pays for, measured

The rules above look discouraging one at a time and are not, once combined: they reward **many
siblings with a byte-identical head**, which is exactly the shape of a map stage. Reproducing the
supervisor/worker pattern at realistic sizes with plain `/api/chat` — a 15,239-token supervisor
alternating with 8,771-token workers whose only difference is a one-line job at the very end
(`fanout_scale.py`):

| Request | matched | cached | Wall |
|---|---|---|---|
| supervisor, cold | 11 | 11 | 23.91 s |
| worker 1 | 13 | 11 | 13.63 s |
| supervisor resume 1 | 15,234 | 8,192 | 11.67 s |
| worker 2 | 8,761 | 8,192 | 1.25 s |
| supervisor resume 2 | 15,236 | 15,234 | 0.44 s |
| workers 3, 4, 5 | 8,761 | 8,761 | **0.41 / 0.42 / 0.37 s** |
| supervisor resumes 3, 4, 5 | 15,236 | 15,236 | **0.46 / 0.42 / 0.41 s** |

**Two rounds of warm-up, then a worker costs 0.4 s instead of 13.6 s** — 34× — and the supervisor
stops paying to come back at all. Note the restores land on 8,192 first and only reach the full
figure on the following round, so the warm-up is two rounds rather than one; that boundary looks
like a chunk size, though nothing in the log says so.

Sibling reuse also survives siblings running back to back. The "you cannot branch off the context
just served" rule applies only while the shared node is still being created: once two conversations
have diverged from a head, four consecutive siblings all restored it with no displacer between them,
0.37 s each (`fanout_rules.py`). Inserting a 28-token throwaway request between them changes
nothing, so no such hack is needed.

**What makes it work, and what breaks it.** The head must be byte-identical — anything
per-invocation near the front (a timestamp, a uuid, a git branch line, a task description placed
before the shared instructions) puts the divergence at the top and there is no shared node to
create. The variable part must be at the end. The shared head itself is safe once two siblings hang
off it — a multi-child trie node is exempt from eviction — so what has to stay within the 8 GiB
snapshot budget is the sum of the *divergent tails*, not the sum of the contexts. Five workers
differing in one line each is nothing; five workers each carrying 40k of their own file reads is
not.

**The wipe is Claude Code's, not Ollama's.** This synthetic run alternated the same shapes at the
same sizes and never once logged `freeing all caches`; the only two occurrences today are the two
sessions that used real subagents. Concurrency was the obvious thing to chase and is now
**refuted**, along with aborted prefills (see the subagent section above). A scripted fan-out of
separate sessions remains the version of this design known to keep its cache in full, and a fresh
session already starts warm on framing it shares with an earlier one — but the in-client version
still saves 37–63 % of its prefill, so the choice between them is a cost trade, not a correctness
one.

### What the bundled `llama-server` can do that Ollama's MLX runner cannot

`/Applications/Ollama.app/Contents/Resources/llama-server` is a current llama.cpp build with three
features relevant here. All measured on `gemma3:1b`; none of it transfers to the MLX runner.

- **KV snapshots to disk work, but only with `--swa-full`.** Without it, `POST /slots/0?action=restore`
  reports `n_restored: 4012`, the slot then matches at similarity 1.000, and the server re-prefills
  all 4,005 tokens anyway — a silent no-op that looks like a success. With `--swa-full`, restore
  takes **3 ms** and the next identical prompt processes **1 token instead of 4,005**, across a
  server restart. Both files were the same 28 MB, so the file is not the difference; the runtime
  flag is. Gemma4 is also a sliding-window model, so the same flag would be required — and what
  full-size SWA costs at 96k is unmeasured and may not fit beside 62 GB of weights.
- **`--cache-ram` parks contexts in host RAM automatically** (default 8192 MiB, `-1` unlimited),
  which is what made A → B → A return `prompt_n=5` before any snapshot was involved. This makes
  disk snapshots largely redundant on that backend.
- **N-gram speculation needs no draft model, and the variant matters more than the idea.** 256
  tokens, greedy, repetitive prompt versus freeform prose:

| `--spec-type` | repetitive | freeform | draft acceptance |
|---|---|---|---|
| none | 276 tok/s | 276 tok/s | — |
| `ngram-cache` | 590 tok/s | **162 tok/s** | 96.5 % / **0 %** |
| `ngram-mod` | 1090 tok/s | 276 tok/s | 59 % |
| `ngram-map-k` | **1429 tok/s** | 277 tok/s | 83 % |

`ngram-cache` — the obvious-sounding one — is a **1.7× slowdown** on prose, drafting constantly and
hitting nothing. `ngram-mod` and `ngram-map-k` pay for themselves on repetition and cost nothing
when there is none, which is the shape you want for code editing. Measured on a 1B; whether the
ratio holds on a 31B is not established.

**`gemma3:1b` cannot be a draft model for gemma4**, settled for free by reading both GGUF headers
rather than loading 62 GB: 6,206 of 262,144 token strings differ (the special-token block: gemma4
spends indices 46–52 on `<|tool>`, `<|tool_call>` and friends where gemma3 has `<unused40>`…), and
`add_bos_token` disagrees. llama.cpp compares vocabularies before it will accept `-md`.

### The depth gate, measured on the 31B at last

The mechanism was verified in Phase 0; what the model *does* when refused was not, and that was the
untested premise the whole pipeline rested on. Two `claude -p` runs against the resident 31B with a
`Stop` hook that refuses exactly once and demands `CLAIM:` / `EVIDENCE: path:lines` / `QUOTE:`
blocks, with `UNKNOWN:` offered as the escape hatch (`scripts/spike/`).

**It complies, immediately, and does not argue.** Neither run pushed back, restated its first
answer, or looped; the second `Stop` arrived with `stop_hook_active=true` and was released. Schema
adherence was perfect in both arms — five well-formed blocks in one, a single `UNKNOWN:` in the
other — so the output is machine-parseable without coaxing.

**It does not necessarily go and look again.** Arm 1 asked what happens to output when a task
exceeds its budget, an answer the model had already gathered in round 1 across seven tool calls.
After the refusal it made **zero** new tool calls and quoted from context, having been told
explicitly not to answer from memory. Arm 2 asked about a JUnit XML reporter that does not exist;
having nothing to quote, it made **12 further tool calls** after the refusal, on top of 26 before
it. So the gate enforces *sufficiency*, not *re-reading*: the model fetches only what it lacks. A
gate that requires fresh evidence must check the transcript for new `tool_use` events itself rather
than ask.

**Quoting from memory drifts, and only a verifier catches it.** Of arm 1's five citations, all five
were correct in content and line range, but only **three were byte-exact**. One was re-indented by
four spaces. One "corrected" a genuinely odd 13-space line in the file to 12 — the model tidying
source it was told to copy verbatim. This is the same defect as the edit failures in §7, arriving
through a different door, and it settles the verifier's design: compare content with indentation
normalised, and report indentation drift as a separate, non-fatal finding.

**No fabrication under a false premise.** Arm 2 rejected the premise in round 1 ("no XML files are
emitted") and, after the refusal, answered with a bare `UNKNOWN:` rather than inventing a citation.
Note the confound: arm 2 also carried `prompts/skeptic_min.md`, which arm 1 did not, so this is
evidence that the gate and that prompt *together* resist fabrication, n=1, and not that either does
alone. It is however the first evidence that skeptic_min binds at all behind Claude Code's own
system prompt, which §10 has listed as unknown since the symlink bug.

**The gate roughly doubles wall-clock.** Arm 1: 171 s to the refusal, 141 s after it, 311 s total.
Arm 2: 287 s then 432 s, 719 s total. One refusal is not a cheap formatting pass — it is a full
turn, re-prefill included, and arm 1's final request re-prefilled 15,439 of 18,700 tokens. Budget a
gated stage at **1.8x to 2.5x** the ungated one, per round, and treat three rounds as a real cost
rather than a safety margin.

### Separating the two confounded arms, and what the real hooks broke

The spike above left two things unmeasured: whether the refusal to fabricate belonged to the gate or
to `skeptic_min.md`, and whether any of it works one level down, on a delegate. Both were rerun
against the *production* hooks (`scripts/cc-depth-contract.py`, `scripts/cc-depth-gate.py`) rather
than the spike's throwaway one, which is how three defects surfaced that a synthetic transcript
could never have shown.

**The prompt was not doing the work.** The false-premise question — "which module writes the JUnit
XML report", of which this repository has none — was rerun with the depth contract and **no**
`skeptic_min.md`. The model rejected the premise anyway, in 110 s and 8 turns, with one claim
citing `bench_lib/report.py:337-350` (byte-exact, `write_report` writing `REPORT.md`) and the
unanswerable half moved to `UNKNOWN:`. So the honesty is the model's, or the contract's; it is not
that prompt's. `skeptic_min.md` and the gate are now separated, n=1 each way.

**A markdown fence read as a fabrication.** That same correct citation was wrapped in
```` ```python ````, and the verifier compared the fence lines as content: verdict `fail`, "quote
not present in bench_lib/report.py". The one verdict that means *the model invented this*, on a
quote copied perfectly. A gate that cries wolf when the model reaches for markdown is a gate that
gets turned off, so an enclosing fence is now stripped (an interior one is content and stays).

**The Stop hook can outrun the transcript.** The gate wrote its verdict at 23:45:24.779 about an
answer the client stamped 23:45:24.728 — 51 ms of lag, and the gate read the file before the answer
reached it. Its verdict: "no claims were stated", about an answer carrying a good one. Everything
downstream inherits that: the refusal text, `gate.json`, the bench measurement. Worse, the
window-of-recent-text fallback then judges *both* drafts after a refusal, re-reporting gaps already
fixed and double-counting `UNKNOWN`s.

**The payload already contains the answer, and on `SubagentStop` it names two transcripts.** Dumping
the raw hook stdin settled all of it (Claude Code 2.1.218):

| field | on `Stop` | on `SubagentStop` |
|---|---|---|
| `last_assistant_message` | the answer, in full | the delegate's answer |
| `transcript_path` | the session | **the parent** — no answer in it yet |
| `agent_transcript_path` | absent | `…/<session>/subagents/agent-<id>.jsonl` |
| `agent_id`, `agent_type` | absent | e.g. `a456612eb07d25138`, `general-purpose` |
| `stop_hook_active` | false, then true | false, then true |

Reading `transcript_path` at `SubagentStop` is reading the parent while it sits inside its `Agent`
call: empty, so the gate refused a delegate whose own file held a well-formed ledger, for "no claims
were stated". Taking `last_assistant_message` removes the race entirely, and
`agent_transcript_path` is also the right *coverage* scope — a sibling delegate's reads are not
evidence that this one looked at anything.

**Gating a delegate works, and the delegate complies too.** Three subagent runs: in the two before
the fix the delegate was refused (for the wrong reason) and re-answered in the demanded form without
arguing, exactly as the parent does. After the fix, both levels are judged on their own answers and
written to their own `gate.json` — `artifacts/depth/<session>/gate.json` for the parent,
`…/<session>/<agent_id>/gate.json` for each delegate — and the whole delegated task passed on the
first try in **83 s**, with no refusal round. Note what that costs when it does refuse: a delegate's
refusal round is paid at the delegate's context, not the parent's, which is the cheap place to pay
it.

### The llama.cpp path, priced at last: snapshots, speculation, and the translator that reaches it

Three things were deferred here for months on the grounds that they needed a gemma4 GGUF, which
does not exist locally — Ollama ships that model as safetensors. Two of them turned out not to need
one, and the third needed only a translator.

**`--swa-full` is the difference between a restore that works and one that lies.** Measured on
`gemma3:1b`'s GGUF (`scripts/phase0/llamacpp/swa_slot_arm.sh`): prefill 25,313 tokens, save the slot,
**kill the server**, start it again, restore, ask the same question.

| | save | restore | answer after restore |
|---|---|---|---|
| `--swa-full` | 25,313 tokens, 115.6 MB, 21 ms | `n_restored=25313`, 19 ms | **0.02 s** |
| default | 25,313 tokens, 115.6 MB, 21 ms | `n_restored=25313`, 20 ms | **2.09 s** — a cold prefill |

Both arms report a complete restore. Only one of them avoids the work, which is the trap: the
success code is not evidence, the timing is. And a second trap sits in front of it — with the
default `--parallel 4` the save writes **28 bytes** and reports `n_saved: 0` with no error at all,
because a unified KV cache has no per-slot state to write. `--parallel 1` is mandatory for any of
this to mean anything.

The save size confirms what the snapshots contain: 4 full-attention layers × 25,313 tokens + 22
windowed layers × 512 = 115.2 MB predicted against 115.63 MB written, 0.4 % out. The same formula
sizes gemma4-31b at 96k under `--swa-full`: 60 layers × 98,304 tokens × 16 KiB = **90 GiB of KV**
beside 58 GiB of weights, against a 107.5 GiB Metal budget (`110100 MiB`, as llama.cpp reports it on
this machine). **It does not fit, and cannot be made to.** The ceiling for gemma4 under `--swa-full`
is ~54k tokens; above that the llama.cpp path has no working disk snapshots, which retires the idea
of parking a 96k agent context on disk. Without `--swa-full` the same context needs only 15.8 GiB —
and restores that do nothing.

**N-gram speculation survives at scale, and it is free.** The old numbers came from a 1B on a
deliberately repetitive prompt, which proves nothing about a coding agent. Rerun on
`Qwopus3.6-35B-A3B-Coder-MTP-Q8` (the only 30B-class GGUF here) with two tasks: rewrite a real
120-line source file with one rename (an edit — mostly verbatim reproduction), and 800 tokens of
prose with no input to echo. Temperature 0, `n_predict` fixed, five settings:

| `--spec-type` | edit | vs none | prose | vs none |
|---|---|---|---|---|
| none | 91.2 tok/s | 1.00× | 91.9 tok/s | 1.00× |
| `ngram-map-k` | 307.0 | 3.37× | 91.9 | 1.00× |
| `ngram-mod` | 356.1 | **3.91×** | 92.5 | 1.01× |
| `draft-mtp` (the model's own head) | 153.4 | 1.68× | 112.2 | 1.22× |
| `ngram-mod,draft-mtp` | 426.7 | **4.68×** | 113.3 | 1.23× |

Every arm produced **byte-identical output** — one md5 across all five edit runs, one across all
five prose runs — so none of this is a quality trade. Three things follow. Speculation and a draft
head **stack**, because they miss on different tokens. `ngram-mod` costs *nothing* on prose, which
kills the plumbing that would have switched it per task: leave it on. And the model's own MTP head,
worth 1.68× on edits, is beaten more than twice over by a scheme that needs no draft weights at all.

The caveat that keeps this honest: this is a 35B **MoE with 3B active**, not a dense 31B. Decode on
a dense model is bound by reading 62 GB of weights per token, which is exactly the cost speculation
amortises across a batch of drafted tokens, so the dense gain should be *larger* — but "should be"
is not a measurement, and gemma4 has no GGUF to measure.

**The translator, and what it caught.** `scripts/anthropic_proxy.py` puts an Anthropic Messages API
in front of any OpenAI endpoint, which is what makes everything above reachable from Claude Code —
`llama-server` speaks OpenAI and Claude Code does not. It runs stdlib-only, streams, translates
tools both ways, and logs each request **on arrival** with its `user-agent`, so per-client
attribution finally exists instead of being reconstructed afterwards. Verified end to end: a real
`Read` tool call answered correctly in 20.7 s over two turns.

Two findings came out of building it. Ollama's OpenAI endpoint returns a reasoning model's output in
`reasoning` with `content` empty, so a translation that reads only `content` hands back a blank
answer that looks like a dead model. And Claude Code sends **`claude-opus-4-8`** regardless of
`--model`, `ANTHROPIC_MODEL`, and every other `ANTHROPIC_*_MODEL` variable — three requests, all
404s, before it fell back. Against Ollama that is worse than an error, because a name it *does*
have but is not the resident variant unloads 62 GB of weights. Hence `--force-model`: the port
decides which model answers, not the client.

### Pointing the gate at two real repositories, and the six defects that fell out

Four unattended runs of `scripts/depth_pipeline.py --adapter review`, two against this repository and
two against a mid-sized TypeScript/Python trading workbench that is larger than the window. Cost is
stable and unremarkable: **24 to 34 minutes** for three stages, of which the claims stage is
two-thirds. Prefix reuse per stage runs 70–96 %, and the claims stage is refused once almost every
time, by design.

What the machinery got wrong mattered more than what the model got wrong.

**A scripted `claude -p` is not the launcher, and the difference is silent.** Every stage of the
first run died in 80 ms with `claude exited 1` and an empty stderr. The client had rejected `--model`
against `enforceAvailableModels` in `~/.claude/settings.json` — a hand-maintained list that a locally
built model is never on, and which in this case still named three models that had been deleted — then
fell back to its cloud default and asked Ollama for `claude-opus-4-8`. The sentence explaining all of
that was on **stdout**, inside the JSON, while the error path read stderr. Any scripted invocation
must supply its own settings file naming its own model; the launcher already did, which is why this
had never been seen.

**An unattended stage on an unfamiliar repository reads until it overruns.** The first assay survey
reached **137,726 tokens against a 98,304 window**, at which point the runner is growing KV into swap
(the model's resident size went 62 → 90 GB and swap passed 5 GB) and every turn costs minutes. It was
reading `package-lock.json`. Wiring the existing `cc-context-guard.py` into the pipeline's own
settings turned that into a 398 s survey at 91 % reuse. The guard's advice had to be forked first:
telling a read-only stage to "write NOTES.md and stop" names a tool it does not have.

**Capping the read moved the failure rather than removing it.** With reads capped at 500 lines, the
claims stage cited line 2619 of a file it had seen to line 500, and invented the code there. The
adversary stage killed it — correctly, and that is the mechanism working — but the run produced
nothing. Two changes followed: the stage stances now say to find a line with `rg` and read around the
hit, and the review of a repository larger than the window is scoped to one subsystem, because a
review of everything is a review of nothing.

**A citation in backticks was refused as a citation of nothing.** The whole first assay review came
back empty because every `EVIDENCE:` line read `` `tools/trade_service.py:1925-1926` `` — as anyone
writing about a path would — and the anchored pattern matched none of them. From the outside a
refusal for punctuation is indistinguishable from a refusal for fabrication, which is the one
confusion this mechanism cannot afford.

**The gate then found three holes in itself, and one of them was serious.** Asked to review its own
source, it reported that `FALSIFICATION:` was checked for existence and never for truth — and proved
it in the same breath, passing two high-severity claims whose falsifications read "Ran a test script
calling evaluate ...", in a run whose own report said `probes_run: 0`. It also caught the probe
counter demanding `c.ok` while the refusal text said "one that fails is fine", which is backwards:
the command that demonstrates a defect usually exits non-zero. After the fix, the next run recorded
**five probes actually run against zero**, and then attacked the fix itself: matching on the program
name alone passes "I ran python3" in any session that ever runs python3. It was right, and it
demonstrated it with a command it really executed.

**What the model is worth on this task, honestly.** Across the runs: on this repository, three real
defects and one non-finding repeated in all three runs — that the proxy is "structurally coupled and
will need duplication to add a backend", quoted from a function signature, which names nothing the
code does wrong and is now ruled out in the review stance. On the trading repository, one accurate
finding (a cash-collateral check defaulting to the on-disk holdings snapshot while a sibling module
passes live holdings) and two honest UNKNOWNs pointing at the two risks worth chasing next. No
surviving fabrication in either, after the parser fix. Thin, true, and cheap enough to run nightly —
which is a different thing from a good reviewer, and should not be described as one.

## 9. How we were blind — hypotheses that were wrong, and what killed them

Kept deliberately, because the wrong turns cost more than the right ones.

| We believed | Actually | Killed by |
|---|---|---|
| MLX gives a ~3.5× runtime speedup | Shared weights; the speedup is a draft model. Runtime worth <5 % | Comparing layer digests |
| Slow turns were memory pressure | Partly cache restores, partly variant thrashing; memory peaks did not predict failures | Correlating peaks against stalls |
| Auxiliary calls were evicting the cache | The real cause was stale `ANTHROPIC_*` in `~/.claude/settings.json` reaching every worker | A logging proxy naming the caller |
| A smaller declared window would force compaction | Nothing consults a threshold; the reserve only fixes the status-line denominator | Reading the binary's gate logic |
| Long quotes caused the edit failures | 47 edits, 0 failures, including 82-line quotes | The probe, in both arms |
| A 5-minute unload was a keep-alive misconfiguration | The messages path ignores body `keep_alive` entirely | Watching the expiry reset each turn |
| A second conversation destroys the first one's prefix cache | It survives, until the trie's paged-out snapshots exceed a budget; then the older is discarded whole | A → B → A at 2.4k (`matched=2371`) and at 55k (`matched=13`) |
| Retained contexts must sum under the 98,304 window | The budget is `maxPagedOutBytes = 8 GiB` of snapshots, hard-coded and unrelated to `num_ctx`; and multi-child nodes are exempt from eviction entirely | 130,718 tokens created without the runner leaving 72 GB, then reading `x/mlxrunner/prefix_cache.go` |
| `PostToolUse` sees every tool call | It never fires on a failed one, so an evidence log built on it omits the failures | Two failing commands, present in the transcript, absent from the hook log |
| Saving the KV cache to disk gives a sub-second resume | Only under `--swa-full`; otherwise restore succeeds and the server re-prefills anyway | `n_restored: 4012` followed by `prompt eval 4005 tokens` |
| N-gram speculation is free decode speed | `ngram-cache` is a 1.7× slowdown on prose; only `ngram-mod`/`ngram-map-k` are free | 0 % acceptance over 398 drafted tokens |
| A 1B could draft for the 31B | 6,206 vocabulary entries differ, and `add_bos_token` disagrees | Reading both GGUF headers |
| The subagent cache wipe was concurrency against an `-np 1` runner | Neither concurrency nor an aborted prefill reproduces it; it follows a mid-edge sibling split after a partial parent restore, and costs one re-prefill, not the session | Two overlapping full-size requests, and a connection cut mid-prefill, both leaving the earlier context warm |
| Delegating to a subagent would inherit the parent's cached prefix | It inherits 27 tokens of 9,740; a subagent's system prompt differs from the first token | `cache miss` on the subagent's opening request |
| The gate reads the answer out of the transcript | The client hands it over as `last_assistant_message`; reading the file races the write and loses by ~50 ms | A verdict timestamped 51 ms after the answer it never saw |
| `SubagentStop` hands the gate the delegate's transcript | It hands over the *parent's*; the delegate's is `agent_transcript_path`, and `agent_id` is how you keep their verdicts apart | Dumping the raw hook payload |
| A byte-exact quote verifies | Not if the model fences it: ```` ```python ```` counted as content and produced a fabrication verdict | The false-premise arm's one and only citation |
| A slot restore that reports success has restored something | Without `--swa-full` it reports `n_restored=25313` and re-prefills anyway; with `--parallel` above 1 it writes 28 bytes and reports `n_saved: 0` | The same prompt answered in 0.02 s and in 2.09 s after identical "successful" restores |
| N-gram speculation might not survive real work | 3.9× on a real edit at 35B, 4.7× stacked with the model's own draft head, and free on prose | Five settings, byte-identical output in all of them |
| `--model` and `ANTHROPIC_MODEL` decide what the client asks for | Claude Code asks for `claude-opus-4-8` anyway, and on Ollama a wrong-but-existing name evicts 62 GB | The proxy's arrival log, three 404s deep |
| A fresh session must re-prefill its framing | It matched 16,387 tokens from an unrelated earlier session, because the head was byte-identical | The same probe's first request |
| `--model` and a settings file are interchangeable | A scripted `claude -p` is vetoed by the *user's* `enforceAvailableModels` and falls back to a cloud model, exiting 1 with an empty stderr | The explanation sitting on stdout, in the JSON, all along |
| Capping reads prevents an overrun | It prevents the overrun and causes a fabrication: the model cites the lines it was not allowed to see | Line 2619 quoted from a file read to line 500 |
| A field the contract demands is a field the gate checks | `FALSIFICATION:` was checked for existence, never for truth; two high claims passed in a run reporting `probes_run: 0` | The gate reviewing its own source |
| Matching a falsification on the program name is enough | Every session runs `python3` or `rg`, so "I ran python3" passes unconditionally | The gate reviewing the fix to the line above |

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
- **Probing an LRU cache oldest-first measures your probe, not the cache.** Fourteen contexts
  probed in creation order reported 0 survivors, because each cold probe evicted another entry
  ahead of itself. Newest-first on the same state found the boundary immediately.
- **Regexes that match the negative case**: a "trouble" pattern matched `truncated=0`, which means
  *not* truncated. Anchor to `truncated=[1-9]`.
- **Transcripts are UTC; the Ollama log is local.** A two-hour offset made session starts look
  unrelated to reloads they had caused. Convert before correlating.

## 10. Still open

- No long session in a large repo has been run on either backend; verification was one
  read-and-answer task per model.
- The 30 → 3.8 tok/s MLX collapse has no established cause. Acceptance decay is insufficient.
- `claim` and `arch` ran 1.7× and 1.3× *slower* on MLX at identical scores, unexplained.
- `skeptic_min.md` and the gate are now separated: the false-premise question was refused without
  the prompt as well as with it. What remains unknown is whether the prompt helps *anywhere* — no
  task has yet been found where its presence changes the answer.
- The sharpened edit rule (gutter, no-resend, cross-file) is unmeasured; the probe tasks exist and
  need both arms once the GPU is free.
- The 26B solves half of `repohard` deterministically and coin-flips the rest (range 20 over 5
  runs). Unquantified in a long session.
- Whether acceptance depends on context *coherence* rather than length. Untested directly.
- ~~What full-size SWA costs gemma4 at 96k~~ **90 GiB of KV against a 107.5 GiB budget: it does not
  fit** (§8). Disk snapshots on the llama.cpp path stop being available above ~54k tokens. What is
  still unmeasured is gemma4 itself, since it has no GGUF; the number is its own geometry applied to
  a formula verified to 0.4 % on gemma3.
- ~~Whether the n-gram gains survive on a 31B and on real editing output~~ **They survive and grow**
  (§8): 3.9× on an edit at 35B, 4.7× stacked with a draft head, free on prose. Measured on an MoE
  with 3B active, though, so the dense-31B number is still unknown — expected higher, not verified.
- ~~The KV bytes per token of a gemma4 snapshot~~ **Settled from the config and the runner's cache
  types** (§8): 160 KiB per token of a node's edge, plus a flat ≤800 MiB per paged-out node from the
  50 windowed layers. What is still unmeasured is the *flat* term end to end — the arithmetic says
  ~9 parked nodes fill the budget, and no probe has counted nodes rather than tokens. The runner's
  `paged_out:` trace line would confirm it directly and needs a restart to enable.
- Whether the 8 GiB constant is reachable through configuration at all. Nothing in the runner's
  command line references it, and it is a `const`.
- Whether the exemption for multi-child nodes leaks: their snapshots are never reclaimed, so a long
  session that branches repeatedly could sit permanently over budget with the eviction loop unable
  to find a victim (`best == nil`, break).
- ~~Whether the depth gate survives contact with a *subagent*, and whether no-fabrication holds
  without `skeptic_min.md`~~ **Both answered** (§8): it gates a delegate through `SubagentStop`, the
  delegate complies with a refusal, and the false premise was refused without the prompt too. The
  payload does point at the delegate's transcript — `agent_transcript_path` — so the recorder needed
  no path convention after all, only to stop reading the parent's.
- Why `prefix_cache.go:255` fails to restore the parent's entry on a later resume and then frees
  every cache. It fired in both multi-subagent sessions and once in eight prior days, but never in
  a synthetic run of the same shapes and sizes, so it belongs to the client's request pattern rather
  than to interleaving as such. Concurrency against an `-np 1` runner is the leading suspect.
- Whether the second-branch rule holds at subagent scale. It is clean at ~3k tokens; at 9.7k the
  cache wipe destroyed the shared node before a third subagent could use it, so the question is
  still open rather than answered in the negative.
