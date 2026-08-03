# Depth pipeline — plan of record

The failure this exists to fix is not code review. It is **premature closure**: the 31B finds a
plausible answer, stops looking, and reports it with the same confidence it would give a verified
one. Review is merely where it shows most. The fix is structural — no conclusion without
machine-checkable evidence, and one narrow job per agent — so the design is task-agnostic and the
task-specific parts are adapters.

Operating constraints and the measurements behind them live in `LOCAL_AGENT_OPS.md` §8. Probes are
in `scripts/phase0/`. This file records **what we are building and why**, and is updated whenever a
measurement invalidates a decision. Two already have.

## 1. Decisions taken, with the evidence that forced them

**Transport: Claude Code's native subagents.** Priced against the alternative rather than assumed.
The two sessions that used real subagents each hit the runner's `freeing all caches` bug once, and
still avoided **63 % and 37 %** of their prefill (138 s and 171 s spent). A scripted fan-out of
`claude -p` sessions saves nearly all prefill instead of half, but costs the in-client visibility
that makes the pipeline usable and debuggable. The tax is two to three minutes per review. Paid.

The two standing explanations for that wipe are **refuted**, not merely unproven: an aborted prefill
(connection cut 4 s into a 15 s prefill) and concurrency (two full-size requests at an `-np 1`
runner, which simply serialized) both left the earlier context restoring in 0.39 s with nothing
logged. It correlates instead with a second sibling splitting a leaf mid-edge after the parent
returned on a partial restore. Unresolved, off the critical path.

**A gated stage costs 1.8x to 2.5x an ungated one.** Measured across the two spike arms: 311 s
against 171 s, and 719 s against 287 s. A refusal is a full turn with its own re-prefill, not a
formatting pass, so three rounds is a budget decision and not a free safety margin. Stages that
routinely need three rounds are mis-specified.

**No parallelism, ever, on this hardware.** One 31B runner fits and it is `-np 1`. "Swarm" means
many specialised agents whose context switches are nearly free — not throughput. Any design that
needs two agents thinking at once is out of scope until the hardware changes.

**Cache budgeting is bytes of divergent tail, not tokens of context.** The MLX prefix cache evicts
against `maxPagedOutBytes = 8 GiB` of paged-out snapshots, hard-coded, with no relation to
`num_ctx`; and a trie node with more than one child is exempt from eviction outright. So a shared
head becomes permanent the moment two agents branch off it, and only the per-agent tails compete for
the budget. The earlier rule in this repo — "agent contexts must sum under 96k" — was inferred from
one coincidence and is **wrong**.

**Every agent prompt shares a byte-identical head.** Reuse is a prefix match from token zero. A
timestamp, uuid, branch name or task description near the front destroys it; all variation goes at
the end. This is also what lets a *fresh* session start warm — 16,387 tokens of 17,871 matched from
an unrelated earlier session.

**Evidence comes from the transcript, not from `PostToolUse`.** That hook never fires on a failed
tool call, so an evidence log built on it silently omits exactly the failures worth catching.

## 2. Architecture

A stage is a narrow agent with a contract. The contract is injected at the end of the prompt via
`UserPromptSubmit` `additionalContext`, keyed by adapter, leaving the head untouched. The agent
works, and on `Stop`/`SubagentStop` a gate reads the transcript, extracts claims, and asks the
verifier whether each claim's citations hold. Unsupported claims produce one consolidated refusal
and the agent continues; up to three rounds, then the stage fails loudly rather than quietly
shipping a guess. Stages hand each other **artifacts on disk**, never a parked context.

```
head (byte-identical)  ->  adapter contract  ->  agent works  ->  Stop gate
                                                       ^              |
                                                       +-- refusal ---+  (max 3)
                                                                      |
                                                              artifact on disk
```

## 3. Components, with acceptance criteria

| # | Component | Done when |
|---|---|---|
| 1 | ~~**Evidence recorder**~~ **done** — `scripts/cc_evidence.py` | Met: on a real session it recovered 189 calls across the parent and both subagent transcripts, 29 failures with their error text |
| 2 | ~~**Verifier library**~~ **done** — `scripts/cc_verify.py`, tests in `scripts/test_cc_verify.py` | Met: replayed against the spike's own five citations it returns 3 `pass`, 1 `indent-drift` (accepted, reported), 1 `retouched` (one line's whitespace altered — refused, but not mislabelled as fabrication). Invented quotes `fail`, missing files are `unverified`, never `pass` |
| 3 | ~~**Claim ledger**~~ **done** — `scripts/cc_ledger.py` | Met: `Contract`/`Claim`/`Evidence` plus five adapters (review, debug, refactor-proposal, ops-perf, bench-audit) express their requirements as data. Claims are read from `claims.jsonl` when the model writes one and parsed out of the reply's blocks when it does not, which is what it actually did under refusal |
| 4 | ~~**Depth gate**~~ **done** — `scripts/cc-depth-gate.py`, tests in `scripts/test_cc_gate.py` | Met: blocks an unsupported answer, releases a supported one, 11 offline checks, 10 of 10 planted fabrications caught, 1 ms per invocation. It blocks **once**, not up to three times (see below) |
| 5 | ~~**Contract injection**~~ **done** — `scripts/cc-depth-contract.py`, wired by `claude-gemma.sh --depth [kind]` | Met: `SessionStart` writes `contract.json`, the first `UserPromptSubmit` returns it as `additionalContext` and no later one repeats it. The head is untouched by construction — nothing is injected ahead of the conversation |
| 6 | ~~**Stage driver**~~ **done** — `scripts/depth_pipeline.py`, tests in `scripts/test_depth_pipeline.py` | Met offline: three stages, artifacts on disk between them, one refusal per stage, head hashed and re-asserted after every stage, a lock so two drivers cannot share one runner. The warm-head figure across a real three-stage run is still unmeasured |
| 7 | ~~**Measurement**~~ **done** — `scripts/depth_fixtures.py`, `scripts/measure_depth_gate.sh` | Met, and the fixtures are the interesting half (below). Contract arms on the resident 31B under `BENCH_REALISM=1`: arch 83 -> **85**/90, claim 23 -> **23**/23, audittrap 75 -> **75**/81, per-task identical on all seven audittrap tasks. No regression anywhere; the +2 on arch is n=1 and is not claimed as an improvement |

Components 1-6 are built and tested offline; `scripts/test_cc_verify.py`, `scripts/test_cc_gate.py`
and `scripts/test_depth_pipeline.py` are the regression checks, and each uses this project's own real
sessions as fixtures rather than invented ones. Only 7 needs the model.

**The gate blocks once, not three times.** The plan allowed up to three rounds; the spike priced a
refused round at 1.8x-2.5x the original turn (141 s and 432 s in the two arms), so a second refusal
would double a stage to buy what the first already bought. `stop_hook_active` short-circuits to
success, which makes a loop impossible by construction rather than by a counter. The finding that
prompted the "check for new tool_use events" requirement is honoured differently and more strictly:
the gate never trusts an instruction to have been followed, because it re-reads every cited span
itself and cross-checks each citation against the line ranges the recorder saw. A quote that matches
the file but that no read in the session covered is named as such.

Two things had to change once the components met each other. The block form originally carried only
file quotes, which made `refactor-proposal` and `ops-perf` unsatisfiable -- both require evidence
kinds the answer format could not express, so the gate would have demanded them forever; the parser
now accepts `command:`, `absence:`, `log:`, `SEVERITY:` and `FALSIFICATION:` lines. And the probe
floor no longer counts `ls`, `find`, `grep` or their kin, because the historical refactor proposal's
entire evidence was five such commands and it would otherwise have cleared a floor written to stop
exactly that.

A verdict vocabulary came out of building 2, and the gate depends on it: `pass`, `indent-drift`
(accepted, reported), `retouched` (content right, whitespace edited — refused, because that drift is
the same defect behind this model's failed edits), `wrong-lines`, `fail`, and `unverified` for
anything that could not be checked. Verifiers fail closed, which is the opposite of the hook
convention in `cc-context-guard.py` and deliberately so: a guard that crashes should let work
through, a verifier that cannot check something must not bless it.

### What the fixtures showed

`scripts/depth_fixtures.py` runs the gate against two answers this project produced before the gate
existed, so neither was written to pass or fail it.

- The **historical refactor proposal** -- typed `AgentResult`, central `Config`, built on
  `grep -c os.environ.get` -- is refused: no claims, no probe.
- A **steelman of it** is also refused, and this is the result worth having. Its three citations were
  made byte-exact and all three verify; it still fails, on the `absence` search it never ran and the
  probe it never ran. The gate refuses the argument, not the formatting.
- The **five spike findings** survive: four verified outright, none called a fabrication. The fifth
  is refused for quote fidelity alone -- one line re-indented by a single space -- which costs a
  round and keeps the finding.

Two things had to be fixed to get an honest number, both of them the same mistake the gate exists to
catch, made by the measurement instead of the model. The comparison script read the per-task file,
which carries no score, and printed `None/None` for every arm -- readable as "the contract changed
nothing". And it checked bench citations against this repository, where a bench's fixture paths do
not exist, marking a correct citation unverified.

### The two arms that were n=1, and the three defects they found

Both remaining arms were rerun against the production hooks rather than the spike's throwaway gate
(`LOCAL_AGENT_OPS.md` §8). That distinction is the point: every defect below needed a real client,
and none of them could have appeared in a synthetic transcript.

- **The false premise, without `skeptic_min.md`.** Still refused, still no invented citation, one
  verified claim and an explicit `UNKNOWN`. The prompt and the gate are separated; the honesty was
  not the prompt's.
- **A delegate, via `SubagentStop`.** It fires, it can block, and the delegate complies with a
  refusal exactly as the parent does — observed three times. Parent and delegate are now judged
  separately, on their own answers, into `artifacts/depth/<session>/gate.json` and
  `…/<session>/<agent_id>/gate.json`. The passing delegated run cost 83 s with no refusal round.

The defects, all three now fixed and covered by tests:

1. A quote wrapped in a ```` ```python ```` fence was compared with the fence as content, and the
   correct citation came back `fail`, the verdict meaning *fabricated*. An enclosing fence is now
   stripped; an interior one is content.
2. The `Stop` hook beat the transcript write by 51 ms and judged the previous turn, reporting "no
   claims were stated" about an answer that had one. The payload's `last_assistant_message` is now
   authoritative, with a quiet-period read of the file as fallback.
3. `SubagentStop` hands over the **parent's** `transcript_path`, which is empty while the parent
   waits inside its `Agent` call. The delegate's is `agent_transcript_path`, which is also the
   correct coverage scope: a sibling's reads are not this delegate's evidence.

### A bench regression found on the way, unrelated to this work

The first audittrap run scored **0 on all six fix tasks in both arms**. It was not the contract and
not the model: the patch it produced for `chat_timeout_dropped` is byte-identical to one that scored
10/10 on 29 July, and the grade moved from `pytest 3/3` to `pytest 0/1`. The interpreter running the
bench had no `pytest` -- none of the three on `PATH` do, only `.venv/bin/python` -- so every fix task
failed collection and was scored zero. Any audittrap fix score produced outside the venv is
worthless, and it looks exactly like a model that cannot fix anything. `run_private_pytest` in both
audittrap and repohard now refuses to grade instead of returning a zero.

## 4. Rules the implementation must not break

- Nothing per-invocation in the prompt head. Variation at the end, always.
- Keep per-agent divergent tails small; they are what competes for the 8 GiB.
- One agent at a time. Sequential by construction, not by convention.
- Never name a model variant other than the resident one; `-64k`/`-96k`/`-128k` are different
  models to Ollama and naming the wrong one evicts a live session.
- Judge cost from `cache hit total=/matched=/cached=` in the server log, not from decode speed.

## 5. Risks that would change this plan

- ~~The gate's premise is untested~~ **Tested 2026-08-01, and it holds** (`LOCAL_AGENT_OPS.md` §8,
  probes in `scripts/spike/`). The model complies immediately, never argues, and holds the
  `CLAIM`/`EVIDENCE`/`QUOTE` schema perfectly. Three consequences are folded into the components
  below rather than left as risk: it does **not** re-read unless it lacks the material, so the gate
  must check the transcript for new `tool_use` events itself; it quotes from memory and drifts on
  indentation, so 2 of 5 citations were not byte-exact; and one refusal costs a full extra turn.
- ~~Whether the gate works one level down~~ **Tested 2026-08-02**: `SubagentStop` gates a delegate,
  the delegate complies, and its verdict is kept apart from the parent's by `agent_id`.
- **Wipe frequency at realistic sizes.** Measured at once per session on 18k parents. If it scales
  with parent size or delegation count, the transport decision flips to scripted `claude -p`
  workers — which is cheap, because every component above is transport-agnostic.
- ~~KV bytes per token is unknown~~ **Resolved by arithmetic, not by a probe**: a paged-out node
  costs a flat ≤800 MiB (50 windowed layers × 1024 tokens × 16 KiB) plus 160 KiB per token of its own
  edge (10 full-attention layers). The 8 GiB budget therefore buys ~9 parked nodes, or one node of
  ~47k tokens. What the driver must keep small is the *number* of parked branch points, which is a
  different instruction than "keep the tails short" — and the flat term has not been measured end to
  end, only derived (`LOCAL_AGENT_OPS.md` §8).
- **The multi-child exemption may leak**: pinned branch points are never reclaimed, so a long
  branching session could sit permanently over budget with the eviction loop unable to find a
  victim.

## 6. Formerly deferred, now measured

All three were run on 2026-08-02 (`LOCAL_AGENT_OPS.md` §8, probes in `scripts/phase0/llamacpp/`).
None of them changes this plan, and one of them closes a door for good.

- **`--swa-full` for gemma4 at 96k: it does not fit.** 90 GiB of KV beside 58 GiB of weights against
  a 107.5 GiB budget; the ceiling is ~54k tokens. Parking a 96k agent context on disk is therefore
  not available on the llama.cpp path at all, and the MLX runner's in-RAM trie stays the only
  mechanism this pipeline can lean on. Measured on gemma3's GGUF, whose save size the formula
  predicts to 0.4 %; also caught two traps that make a restore *look* successful when it is not
  (default `--parallel` saves nothing; without `--swa-full` the restore re-prefills).
- **N-gram speculation is a 3.9× decode win on real editing output at 35B, 4.7× stacked with a draft
  head, and free on prose** — byte-identical output in all five settings. It does not help this
  pipeline directly, because the gate's cost is *prefill* (a refusal round re-prefills; §8 measures
  1.8–2.5×), and speculation accelerates decode. It matters the moment a stage is generating patches
  rather than reading, and it costs nothing to leave on.
- **The translating proxy exists and works**: `scripts/anthropic_proxy.py`, 10 offline tests, driven
  end to end by Claude Code through a real tool call. Two things it bought immediately: per-client
  attribution logged on arrival rather than reconstructed, and `--force-model`, after the client was
  caught asking for `claude-opus-4-8` regardless of every model setting — the failure mode that
  evicts a resident 62 GB runner.

## 7. State of the harness, measured over runs 17–21

Every line below is from a live headless run against the local 35B, reviewing this repo's own
off-switch tamper rule. Nothing here is inferred from a unit test. 285 tests pass, which is a
statement about the parts, not about the whole.

### What works

**A run terminates cleanly and says what it failed to establish.** Run 21 finished with
`is_error: false`, 22 turns, no context overflow. Its claims stage was refused three times, the flow
gave up on it, and the session's final answer opened: *"The review flow is not finished. The claims
stage was refused 3 times and the flow has given up on it."* That is the designed outcome for a stage
that cannot meet its contract — an honest non-answer rather than a plausible one.

**The survey stage.** Accepted in 258 s on 2 tool calls. It was 141 calls in run 20 before stages were
given budgets of their own; the survey's is 60 and it now finishes well inside it.

**Claims are parsed, and judged one at a time.** Run 21 round 4 produced 7 claims and 1 declared
unknown, carrying 14 command citations; 6 claims stood and 1 was reported unverified, by name and with
the reason. Compare run 18, where a stage with eight findings was told it had made none. The verifier
now reads bold headers, numbered findings under a heading, paragraphs under a bare `CLAIM` heading,
citations in a claim's own last sentence, probes reported in prose, quotes carrying gutters, and a
dozen other shapes models actually write.

**A refused probe counts as evidence of refusal.** A rule that denies things is proved by denying, and
the stage under review spent 11 of its calls collecting exactly that.

**Stage accounting is exact.** Every tool call is attributed to the stage that made it, because the
client's `agent_id` distinguishes a subagent's calls from its parent's. Flow state is serialised with
an `flock`, so concurrent hooks no longer overwrite each other.

**The budget ends a runaway stage in one turn.** Run 20's survey hit 141 calls, received one refusal,
and answered immediately. The same mechanism in run 18 produced 220 refusals.

### What does not work

**A stage that verifies six findings still delivers nothing.** `claims` is blocking, so when it is
given up on the flow ends and the adversary never runs. Run 21 established six verified findings about
a real rule and shipped none of them: they exist only in a refused round's ledger. This is the single
biggest gap between the harness and the thing a user wants.

**The review adapter demands a file quote and this stage never produced one.** Four rounds, 25
citations, all of them commands. Every round was refused partly for `requires file_quote evidence and
none was given` — the requirement is right for a code review, and the contract is losing the argument.

**The same finding, restated, counts three times.** Run 21's round 1 wrote its four findings in three
formats — bold, plain, then with line numbers — and all 14 parsed as separate claims, over the cap of
12, with no evidence attached to any of them. There is no deduplication.

**A round can end mid-thought.** Round 3 of run 21 wrote 454 characters and stopped on a sentence
about what it was going to do next. It cost a round out of three.

**A whole answer can vanish into one tool call.** Run 20's claims stage spent all 16,384 tokens of an
answer on a single call's arguments; the proxy dropped it as truncated and the only thing on record was
the note that it had been cut. Both proxy paths now log what they discard, which makes this findable
rather than mysterious — it does not stop it happening.

**The context ceiling is a cliff, not a slope.** Run 18 died at 98,342 tokens against a 98,304 window
and run 20 at 98,950 — the second *with* a declared budget of 90,112, because Claude Code estimates
tokens and llama-server tokenises them, and the two disagree by about 10% at that size. A quarter of
the window is now held back and run 21 survived. This is a margin chosen by measurement, not a fix.

**Two `Write` attempts per stage, refused.** Stages that only read keep trying to write their ledger
to a file. The rule catches it every time; the instinct does not go away.

### Not yet exercised

The `implement` flow has not been run against a real feature since the harness changed under it. The
interactive path (`claude-gemma.sh --flows`, `/review`, `/implement`) has the same wiring and the same
tests, but the runs above are all headless.
