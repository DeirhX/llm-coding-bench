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
| 3 | **Claim ledger** — schema (claim, evidence pointers, verdict, unknowns) plus per-task adapters declaring required evidence | `arch`, `claim` and `audittrap` adapters express their requirements without schema changes |
| 4 | **Depth gate** — `cc-depth-gate.py` on `Stop` and `SubagentStop`, one consolidated refusal, ≤3 rounds, `stop_hook_active` safe | Blocks an unsupported answer, releases a supported one, never loops. Where fresh evidence is required, it verifies new `tool_use` events appeared since its last refusal rather than instructing the model to look again |
| 5 | **Contract injection** — `UserPromptSubmit`, adapter-keyed, head untouched | Prefix match on turn 1 stays at the warm-session figure |
| 6 | **Stage driver** — sequential stages, artifacts between them, tails within budget | A three-stage run keeps the shared head warm throughout |
| 7 | **Measurement** — the gate on `arch`/`claim`/`audittrap` under `BENCH_REALISM=1`, with the historical `AgentResult`/`Config` plan as a negative fixture | The negative fixture is refused; scores on the positives do not regress |

Components 1–5 are client-side code with no GPU dependency and can be built while the machine is
busy. Only 6 and 7 need the model. 1 and 2 are built and tested; `python3 scripts/test_cc_verify.py`
is the regression check, and it uses the spike's real citations as its fixture rather than invented
ones.

A verdict vocabulary came out of building 2, and the gate depends on it: `pass`, `indent-drift`
(accepted, reported), `retouched` (content right, whitespace edited — refused, because that drift is
the same defect behind this model's failed edits), `wrong-lines`, `fail`, and `unverified` for
anything that could not be checked. Verifiers fail closed, which is the opposite of the hook
convention in `cc-context-guard.py` and deliberately so: a guard that crashes should let work
through, a verifier that cannot check something must not bless it.

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
- **Wipe frequency at realistic sizes.** Measured at once per session on 18k parents. If it scales
  with parent size or delegation count, the transport decision flips to scripted `claude -p`
  workers — which is cheap, because every component above is transport-agnostic.
- **KV bytes per token is unknown**, so the 8 GiB budget is only bracketed at 7.5k–19k tokens of
  retained tail. The driver currently cannot be given a precise number.
- **The multi-child exemption may leak**: pinned branch points are never reclaimed, so a long
  branching session could sit permanently over budget with the eviction loop unable to find a
  victim.

## 6. Deferred, and what would revive them

- `--swa-full` KV size for gemma4 at 96k, and whether it fits beside 62 GB of weights. Revived only
  if disk-backed context snapshots become necessary — i.e. if the 8 GiB budget proves too tight.
- `ngram-mod` / `ngram-map-k` on the dense gemma4 GGUF against MLX+MTP decode, on real editing
  output rather than a repetition fixture. Revived if decode, not prefill, becomes the bottleneck.
- Anthropic-to-OpenAI translating proxy. Only if the llama.cpp path wins; it would also finally give
  per-client attribution in the logs.
