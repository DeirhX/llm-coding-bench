#!/bin/zsh
# Launch Claude Code against a local Gemma 4 served by Ollama.
#
# No proxy is involved. Ollama 0.32.5 serves Anthropic's /v1/messages directly, so
# ANTHROPIC_BASE_URL pointed at the Ollama port is the whole integration.
#
# THE CHOICE THIS SCRIPT EXISTS TO GET RIGHT is which weights. The runtime is not a choice
# at all, and two earlier versions of this script got that wrong in opposite directions.
#
# Ollama picks the engine from how the model is packaged, not from anything a caller says.
# gemma4:31b-it-bf16 ships as a GGUF blob and runs under llama-server. The draft checkpoint
# ships as 1245 safetensors layers and runs under `ollama runner --mlx-engine`. There is no
# GGUF build of the draft model and no safetensors build of the dense one, so the two axes
# are perfectly confounded in the tags available here and no measurement can separate them.
#
# That also means gemma4:31b-mlx-bf16 and gemma4:31b-coding-mtp-bf16 are the same model:
# 1245 of 1247 layer digests match, and the two that differ are a config JSON and a licence
# file. Offering them as separate options, as this script briefly did, was offering one
# thing under two names. 'mlx' is kept only as an alias so old muscle memory does not error.
#
# The honest claim is therefore narrow: 'fast' is ~3.4x quicker at short context and ~1.9x
# at 100k, achieved by some combination of an embedded ~0.4B draft network doing speculative
# decoding and the MLX runtime. Which of those contributes what is unknown and unknowable
# with these tags.
#
#   accurate   gemma4:31b-it-bf16, dense, no draft, GGUF under llama-server. The best
#              scores measured here, and the only 31B build with vision. 8.1 tok/s at short
#              context, 6.2 at 100k. It enforces its context window: overflow is truncated,
#              loudly, keeping the first 5 tokens and the tail.
#
#   fast       the draft checkpoint. 27 tok/s at short context, 20 at 32k, 15.6 at 64k,
#              11.9 at 100k -- the advantage decays as the draft gets rejected more often,
#              from 3.4x down to 1.9x, but it never inverts. Costs: 10 audittrap points
#              (one real-fix task), 8 pyhard points, and vision. Ties on repohard 74/80,
#              claim 22/23, arch 83/90, and holds false-bug traps at 20/20 across 3 runs.
#
# The other things Claude Code cannot know by itself:
#
#   num_ctx     Claude Code never sends one, and the MLX runner ignores it in request
#               options even when sent. Only a Modelfile PARAMETER binds it, which is why
#               the pinned variants exist. 65536 is the window most scores were measured at.
#               Note that overflowing a pinned window does not error: Ollama silently
#               truncated a 132k prompt to 65,539 tokens in testing, discarding half the
#               context, and a truncated prefix also measurably hurts draft acceptance.
#
#   overflow    These two engines disagree about what a context window means, and the
#               difference matters more than the number does. llama-server enforces it and
#               says so. The MLX runner does not: a session here sent an 80,774-token
#               prompt to a model pinned at 65536 and it processed all 80,774, growing the
#               KV cache to a peak of 121.5 GiB on a 128 GB machine. So on 'fast' the risk
#               of a large context is memory exhaustion, not silent truncation.
#
#   small model Claude Code makes background calls of its own: session titling, and a recap
#               when you step away. Pointed at the main model they arrive with a different
#               prompt shape, and a competing prefix displaces the conversation's cached one.
#               Measured on the recap: cache reuse fell from 88.7% to 27.5%, costing 108
#               seconds to reprocess 54k tokens. Routing what can be routed at a small
#               separate model gives it its own runner and leaves the cache alone. How many
#               prefixes the runner will hold at once has not been established here, so the
#               claim is only that a large competing prefix evicts, which is measured.
#
#   the prompt  The 31B scores 0/20 on false-bug reports with no system prompt and 20/20
#               with 63 generic words, at no cost to anything else, on both checkpoints. So
#               the 31B gets that prompt appended by default. The 26B does NOT: every prompt
#               tested made it worse, and the same prompt that fixes the 31B cost the 26B 10
#               points on real repairs while leaving its traps at 0/20.
#
#   preloading  A 62 GB model takes over two minutes to load cold. Doing that inside the
#               first turn looks like a hang. Warm it before handing over.
#
#   eviction    Two of these do not fit in memory at once. Stop the siblings first rather
#               than discovering it during a request.
#
# Session settings are passed as inline JSON so your ~/.claude/settings.json is never
# modified: this launcher and your existing qwen setup coexist.

set -uo pipefail

# $0 is however this was invoked, and dirname does not follow symlinks: through the
# claude-gemma symlink in ~/.local/bin this resolved to /Users/deirh/.local, both
# prompt files failed their -f test, and a whole session ran with no skepticism
# prompt while the banner reported "prompt: none" as though that were the request.
# :A resolves the link first, so the two :h steps land in the repository either way.
ROOT="${0:A:h:h}"
OLLAMA_URL="${OLLAMA_HOST_URL:-http://127.0.0.1:11434}"
SKEPTIC_PROMPT="$ROOT/prompts/skeptic_min.md"
EDIT_PROMPT="$ROOT/prompts/edit_discipline.md"
# Absorbs Claude Code's periodic background call so it does not evict the conversation's
# prefix cache. 1.0 GB resident alongside the 78 GB model, and loading it was verified not
# to evict that model. Must not be a thinking model: qwen3:1.7b was tried first and spent
# its whole token budget in a <thinking> block, returning empty text on a trivial prompt.
SMALL_MODEL="${CLAUDE_GEMMA_SMALL_MODEL:-gemma3:1b}"

SIZE="31b"
CTX="64k"
WEIGHTS="fast"
RUNTIME="cpp"
USE_PROMPT="auto"
USE_EDIT_RULE="auto"
YOLO=0

usage() {
  cat <<'USAGE'
Usage: claude-gemma [31b|26b] [fast|accurate] [64k|96k|128k|max] [mlx] [options] [-- claude args]

  31b            gemma4 31B, dense-attention. Stable and disciplined. (default)
  26b            gemma4 26B-A4B, sparse. Faster, but solves only half the repohard suite
                 deterministically and coin-flips the rest. Accurate weights only.

  fast           the draft-equipped checkpoint: speculative decoding, 2-3.4x faster
                 depending on context. Loses 10 audittrap points, 8 pyhard points and
                 vision; ties on repohard, claim, arch and trap discipline. (default)
  accurate       the plain dense model: best measured quality, accepts images, 8.1 tok/s.

  64k            65536 context, the size most scores were measured at. (default)
  96k            98304 context. A middle setting; see the note on enforcement below.
  128k           131072 context for large repositories. Quality there is unmeasured, and
                 the draft advantage is down to ~1.9x by 100k tokens.

                 On 'fast' and 'mlx' these numbers are advisory: a prompt of 80,802 tokens
                 went to the 64k variant and all 80,774 unmatched tokens were prefilled,
                 with no truncation and no error. The window will not cap a conversation
                 or bound memory, which grows with the tokens in play at ~0.33 MB each.
                 Only 'accurate' enforces its window, and it truncates loudly.
  max            the model's full 262144. Largest window, least evidence behind it.

                 A window applies to conversations started after this launch. Resuming
                 an earlier conversation keeps the model it was created with, whatever
                 is asked for here: a session launched at 96k ran all 127 of its turns
                 against the 128k model, and Ollama loaded those weights to serve them.
                 Start a new conversation for a change of window to take effect.

  mlx            Ollama's MLX runner instead of llama.cpp. Same weights as 'fast', within
                 5% on speed, and far less exercised. Kept for comparison, not advised.

  --prompt       Force the skepticism system prompt on (default: on for 31b only).
  --no-prompt    Force it off.
  --edit-rule    Force the edit-discipline rule on. It follows --prompt by default.
                 Addresses a measured fault: 4 of 31 edits in one session failed,
                 three of them by quoting 33 to 194 lines of a file at an
                 indentation guessed one level out. Its own effect is unmeasured.
  --no-edit-rule Force it off.
  --depth [kind] Gate the session: a task contract goes in, and an answer whose
                 citations do not check out is refused once, with every gap listed.
                 kind is one of review, debug, refactor-proposal, ops-perf,
                 bench-audit (default review). touch /tmp/cc-depth-off to lift.
  --yolo         Bypass every permission check for the session. Edits, writes and
                 shell commands run unattended, in the current directory, with no
                 confirmation. See the warning it prints before you use it.
  --list         Show the installed variants and exit.
  -h, --help     This message.

Anything after -- is passed through to claude untouched.

Examples:
  claude-gemma                        31B draft weights, 64k, skepticism prompt on
  claude-gemma accurate               31B dense: best quality and image support, 3x slower
  claude-gemma 128k                   31B draft at 131072 for a large repository
  claude-gemma 26b --no-prompt        26B, no system prompt
  claude-gemma --all-tools            keep sub-agents, tasks and cron (+13k tokens/turn)
  claude-gemma --no-guard             allow unbounded reads and overrunning the window
  claude-gemma -- --continue          resume the previous session
USAGE
}

# Every request carries every tool's schema, so tools that cannot be used here are a
# per-turn tax. Measured at 13,276 tokens for the 16 dropped below, against 4,733 for
# everything that remains including the system prompt. Those counts come from a 1B model
# of the same family, since no tokenizer endpoint exists for the 31B: the live session's
# own client/server gap implies the true saving is nearer 9k. The ranking of tools by cost
# is unaffected either way, and Workflow alone is a third of it.
# Re-measured 30 Jul by capturing a real *interactive* request through a pty, because a `claude -p`
# capture ships a different tool set (8 tools, no plan mode) and understated the framing at 6,418.
# Interactive with the 16 tools below withheld came to 9,093 tokens; withholding the five added on
# the second line brings it to 4,477, so half the remaining boilerplate was in tools this workload
# never calls: AskUserQuestion 1,122, EnterPlanMode 944, ExitPlanMode 550, NotebookEdit 435, Skill
# 426 -- and dropping Skill also removes the 1,094-token skills catalogue, which the client injects
# as a separate system message only when the tool is present. What remains is Bash, Read, Edit,
# Write, WebSearch, WebFetch at 2,021 tokens, plus 2,014 of Claude Code's own prose and 329 of ours.
# The cost is real if you want plan mode or structured questions back: --all-tools restores them.
# The context guard is a PreToolUse hook, and it exists because the prompt version of the same rule
# demonstrably does not hold: with the read discipline in force, one session still read
# benches/pyhard/bench.py twice at ~11,940 tokens each, and 82% of that conversation was tool
# results. The hook refuses an unbounded read of a file over 500 lines, refuses a re-read of a file
# unchanged since it was last read, and past 80% of the window refuses anything bulky while leaving
# Write and Edit available so findings can be recorded before stopping. Verified against a fake
# endpoint: a deny is honoured under --dangerously-skip-permissions and its text reaches the model
# as the tool result. Lift it for a session with `touch /tmp/cc-guard-off`, or launch --no-guard.
PRE_TOOL=()
PRINT_SETTINGS=0
GUARD=1
LIFTABLE=0
DEPTH=0
DEPTH_ADAPTER="review"
FLOWS=0
LEAN_TOOLS=1
# Task is how a flow's stages run, and TaskCreate/Update are how the client shows their progress,
# so a flow session keeps them and pays the system-prompt tokens for them.
UNUSED_TOOLS="Workflow,Agent,TaskCreate,TaskUpdate,TaskList,TaskGet,TaskStop"
UNUSED_TOOLS="$UNUSED_TOOLS,TaskOutput,ReportFindings,SendMessage,CronCreate"
UNUSED_TOOLS="$UNUSED_TOOLS,CronList,CronDelete,ScheduleWakeup,EnterWorktree,ExitWorktree"
UNUSED_TOOLS="$UNUSED_TOOLS,AskUserQuestion,EnterPlanMode,ExitPlanMode,Skill,NotebookEdit"

CLAUDE_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    31b|31B) SIZE="31b"; shift ;;
    26b|26B) SIZE="26b"; shift ;;
    fast|draft|mtp) WEIGHTS="fast"; shift ;;
    accurate|dense|quality) WEIGHTS="accurate"; shift ;;
    64k|64K) CTX="64k"; shift ;;
    96k|96K) CTX="96k"; shift ;;
    128k|128K) CTX="128k"; shift ;;
    max|MAX|native) CTX="max"; shift ;;
    mlx|MLX) RUNTIME="mlx"; shift ;;
    cpp|CPP|llamacpp) RUNTIME="cpp"; shift ;;
    --prompt) USE_PROMPT="on"; shift ;;
    --no-prompt) USE_PROMPT="off"; shift ;;
    --edit-rule) USE_EDIT_RULE="on"; shift ;;
    --no-edit-rule) USE_EDIT_RULE="off"; shift ;;
    --yolo|-y) YOLO=1; shift ;;
    --flows) FLOWS=1; DEPTH=1; shift ;;
    --no-flows) FLOWS=0; shift ;;
    --lean-tools) LEAN_TOOLS=1; shift ;;
    --all-tools) LEAN_TOOLS=0; shift ;;
    --guard) GUARD=1; shift ;;
    --no-guard) GUARD=0; shift ;;
    --depth)
      DEPTH=1; shift
      case "${1:-}" in
        review|debug|refactor-proposal|ops-perf|bench-audit) DEPTH_ADAPTER="$1"; shift ;;
      esac ;;
    --no-depth) DEPTH=0; shift ;;
    --list)
      echo "Installed Gemma variants:"
      ollama list 2>/dev/null | awk 'NR==1 || /^gemma4-(31b|26b)-(coding|mtp|mlx)-|^gemma4-coding:/ { print "  " $0 }'
      exit 0 ;;
    -h|--help) usage; exit 0 ;;
    --liftable) LIFTABLE=1; shift ;;
    # Prints the settings this launch would use and exits. The hooks are assembled here at
    # runtime, so reading the source is not the same as knowing what gets registered: three
    # fixes in a row went in behind matchers that never routed the tool they were about.
    --print-settings) PRINT_SETTINGS=1; shift ;;
    --) shift; CLAUDE_ARGS=("$@"); break ;;
    *) CLAUDE_ARGS+=("$1"); shift ;;
  esac
done

# No draft build of the 26B has been pulled or measured, and no dense build exists on MLX,
# so refuse those combinations rather than silently substituting weights nobody has scored.
if [[ "$SIZE" == "26b" && "$WEIGHTS" == "fast" ]]; then
  echo "note: no draft build of the 26B exists here; using its dense weights." >&2
  WEIGHTS="accurate"
fi
if [[ "$RUNTIME" == "mlx" ]]; then
  if [[ "$SIZE" != "31b" ]]; then
    echo "error: the MLX builds here are 31B only. Drop 'mlx' or use '31b mlx'." >&2
    exit 1
  fi
  if [[ "$WEIGHTS" == "accurate" ]]; then
    echo "error: there is no dense MLX build. The MLX tags are the draft checkpoint," >&2
    echo "       so 'accurate mlx' would not be the model you asked for." >&2
    exit 1
  fi
fi

if [[ "$SIZE" == "26b" ]]; then
  [[ "$CTX" == "max" ]] && MODEL="gemma4-coding:26b-a4b" || MODEL="gemma4-26b-coding-${CTX}"
elif [[ "$RUNTIME" == "mlx" ]]; then
  [[ "$CTX" == "max" ]] && MODEL="gemma4-coding:31b-mlxbf16" || MODEL="gemma4-31b-mlx-${CTX}"
elif [[ "$WEIGHTS" == "fast" ]]; then
  [[ "$CTX" == "max" ]] && MODEL="gemma4-coding:31b-mtp" || MODEL="gemma4-31b-mtp-${CTX}"
else
  [[ "$CTX" == "max" ]] && MODEL="gemma4-coding:31b" || MODEL="gemma4-31b-coding-${CTX}"
fi

# Claude Code sizes its own auto-compaction against the model's context window, and with
# no value supplied it assumes a Sonnet-shaped one -- far larger than anything here. The
# consequence was measured: a session reached 99,746 tokens against a 98,304 window before
# compaction even triggered, and compaction then has to re-send the whole conversation,
# which took over four minutes on a cold cache and was abandoned twice. Telling it the
# truth moves that work to a point where it is still cheap.
case "$CTX" in
  64k)  CTX_TOKENS=65536 ;;
  96k)  CTX_TOKENS=98304 ;;
  128k) CTX_TOKENS=131072 ;;
  max)  CTX_TOKENS=262144 ;;
  *)    echo "internal error: no token count for CTX=$CTX" >&2; exit 1 ;;
esac

if [[ "$USE_PROMPT" == "auto" ]]; then
  [[ "$SIZE" == "31b" ]] && USE_PROMPT="on" || USE_PROMPT="off"
fi
# Follows the skepticism prompt rather than defaulting on by itself. The 26B loses fix
# points to every prompt measured so far, and this rule has no measurement of its own
# yet, so it does not get to be the first thing switched on there by default.
if [[ "$USE_EDIT_RULE" == "auto" ]]; then
  USE_EDIT_RULE="$USE_PROMPT"
fi

# A prompt file that cannot be found used to be indistinguishable from --no-prompt.
# Refuse rather than launch disarmed: the skepticism prompt is the difference between
# 0 and 20 out of 20 on false-bug traps for this model.
if [[ "$USE_PROMPT" == "on" && ! -f "$SKEPTIC_PROMPT" ]]; then
  echo "error: skepticism prompt not found at $SKEPTIC_PROMPT" >&2
  echo "       pass --no-prompt to run without it deliberately." >&2
  exit 1
fi
if [[ "$USE_EDIT_RULE" == "on" && ! -f "$EDIT_PROMPT" ]]; then
  echo "error: edit-discipline prompt not found at $EDIT_PROMPT" >&2
  echo "       pass --no-edit-rule to run without it deliberately." >&2
  exit 1
fi

# Gemma emits LaTeX, a habit from its Gemini-family training, and a terminal renders none
# of it: "$55 \times \text{result size}$" arrives on screen exactly like that. An earlier
# version of this rule said "arithmetic, formulas and units", and the model complied with
# it exactly, then wrote "the task $\rightarrow$ id rename" in prose, because an arrow is a
# symbol and not a formula. The scope was the defect, so the rule now covers any such
# markup wherever it appears.
# Claude Code refuses --append-system-prompt together with --append-system-prompt-file, so
# the two are composed into a scratch file per launch. skeptic_min.md therefore stays
# byte-identical to the file the 20/20 trap result was measured against. Note the composed
# prompt is 36 words longer than the file the trap result was measured against, or 179 with
# the edit rule on, and on the 26B every added word has so far cost fix points.
FORMAT_RULE='Write in plain text that a terminal can display, including symbols in prose: no LaTeX, MathJax or dollar-delimited markup anywhere. Write "task -> id" and "55 * result size", never "$\rightarrow$" or "$55 \times \text{result size}$".'
COMPOSED_PROMPT="${TMPDIR:-/tmp}/claude-gemma-system-prompt.md"

PROMPT_ARGS=()
typeset -a PROMPT_PARTS
if [[ "$USE_PROMPT" == "on" && -f "$SKEPTIC_PROMPT" ]]; then
  { cat "$SKEPTIC_PROMPT"; printf '\n%s\n' "$FORMAT_RULE"; } > "$COMPOSED_PROMPT"
  PROMPT_PARTS=("$(basename "$SKEPTIC_PROMPT")" "plain-text formatting rule")
  PROMPT_ARGS=(--append-system-prompt-file "$COMPOSED_PROMPT")
fi
# Appended after the skepticism prompt when both are on, and able to stand alone when
# only it is wanted, which is what an arm that isolates it needs.
if [[ "$USE_EDIT_RULE" == "on" && -f "$EDIT_PROMPT" ]]; then
  if (( ${#PROMPT_ARGS} )); then
    { printf '\n'; cat "$EDIT_PROMPT"; } >> "$COMPOSED_PROMPT"
  else
    cat "$EDIT_PROMPT" > "$COMPOSED_PROMPT"
    PROMPT_ARGS=(--append-system-prompt-file "$COMPOSED_PROMPT")
  fi
  PROMPT_PARTS+=("$(basename "$EDIT_PROMPT")")
fi

# Fall back to the main model rather than failing, so a missing small model costs cache
# thrash rather than a launcher that will not start.
SMALL_SLOT="$MODEL"
SMALL_NOTE="not installed -- background calls will evict the conversation cache"
if ollama list 2>/dev/null | awk -v m="$SMALL_MODEL" '$1 == m { f=1 } END { exit f ? 0 : 1 }'; then
  SMALL_SLOT="$SMALL_MODEL"
  SMALL_NOTE="$SMALL_MODEL"
fi

# Announced before the load, not after: a cold 62 GB model takes over two minutes, and
# knowing what is being loaded is most useful while waiting for it rather than once it
# has arrived.
echo "model:  $MODEL"
if [[ "$SIZE" == "26b" ]]; then
  echo "weights: 26B-A4B sparse, dense-attention weights"
elif [[ "$WEIGHTS" == "fast" ]]; then
  echo "weights: draft-equipped -- 2-3.4x faster by context, no vision, 10 audittrap"
  echo "         points thinner. 'claude-gemma accurate' for the best measured quality."
else
  echo "weights: plain dense -- best measured quality and image support, ~8 tok/s."
fi
[[ "$RUNTIME" == "mlx" ]] && echo "runtime: MLX (within 5% of llama.cpp; kept for comparison)"
if (( ${#PROMPT_ARGS} )); then
  echo "prompt: ${(j: + :)PROMPT_PARTS}"
else
  echo "prompt: none (raw model behaviour, including LaTeX markup)"
fi
echo "small:  $SMALL_NOTE"
# What the client counts and what the server renders are different numbers. Measured on
# a live session: the client called it 99,005 input tokens for the turn that Ollama
# rendered as 111,186. The 12,181 gap is tool schemas, system prompt and template, none
# of which the client counts -- so declaring the true window guarantees the server
# overflows first, and it did, by 13%, silently. On the MLX runner that shows up as a KV
# cache growing past its pinned size (62 GB resident becoming 82) and the machine paging,
# not as an error. The reserve is larger than the measurement because MCP tool schemas vary
# per session.
#
# This reserve does NOT make the client compact earlier: nothing here compacts by itself,
# for the reasons set out below. What it buys is an honest denominator, so the percentage in
# the status line reaches 100 at about the point the rendered prompt reaches the real window,
# and a human watching that number acts before the overflow rather than after it.
# The reserve must exceed the framing, or declaring a window is worse than useless: it hands the
# client a ceiling whose own arithmetic still overflows the runner. Measured framing is 4,733
# tokens lean and 18,009 with every tool, so the old full-tools reserve of 16,384 left the client
# free to render 99,929 against a 98,304 window -- 1,625 over, which is exactly the overflow that
# costs 2-5x on decode. Each figure below is the measurement plus roughly 3k of slack, because MCP
# tool schemas vary per session and the cost of reserving too much is one earlier compaction.
if (( LEAN_TOOLS )); then
  CTX_RESERVE=8192
else
  CTX_RESERVE=20480
fi
# A fixed reserve covers the framing the client never counts. It does not cover the second error,
# which is far larger than it was thought to be: the client counts four characters to a token, which
# is the rule for prose, and a coding session carries source, JSON, paths, diffs and command output.
# Twelve real transcripts put through llama-server's own tokeniser came back at 2.76 to 3.21
# characters per token, so the client's estimate can be 1.45x low -- not the 10% written here before.
#
# Three quarters does not survive that: 98,304 declared, believed, is 142,000 tokens offered to a
# 131,072-token window, and runs 18, 20 and 25 all died a few thousand tokens past the end. 65%
# survives the worst measured ratio with room to spare even if compaction never fires. So the ceiling
# is whichever is lower, the window less the framing or 65% of it.
CTX_FRAMED=$(( CTX_TOKENS > CTX_RESERVE * 2 ? CTX_TOKENS - CTX_RESERVE : CTX_TOKENS ))
CTX_COUNTED=$(( CTX_TOKENS * 65 / 100 ))
CTX_DECLARED=$(( CTX_FRAMED < CTX_COUNTED ? CTX_FRAMED : CTX_COUNTED ))
echo "window: $CTX_TOKENS tokens; Claude Code told $CTX_DECLARED, leaving room for the tools and"
echo "        framing it never counts and for the 45% it counts differently from the runner"
if (( LEAN_TOOLS )); then
  echo "tools:  6 sent, 21 withheld (sub-agents, tasks, cron, worktrees, plan mode,"
  echo "        skills, notebooks, structured questions). Framing is 4,477 tokens a turn"
  echo "        instead of 9,093, measured on a real interactive request. --all-tools"
  echo "        restores plan mode and the rest."
else
  echo "tools:  everything sent, costing 16,168 tokens of every request"
fi
# A survey stage ran `touch /tmp/cc-guard-off` to get past a refusal and left it there, so every
# session started afterwards ran unguarded and nothing said so. The switch is cleared at launch and
# the guard now refuses any tool call that would write it, which leaves it working for the person
# here and unavailable to the model.
if (( GUARD )); then
  if [[ -e /tmp/cc-guard-off || -e /tmp/cc-depth-off ]]; then
    rm -f /tmp/cc-guard-off /tmp/cc-depth-off
    echo "note:   a stale off-switch was left in /tmp and has been cleared"
  fi
  echo "guard:  unbounded reads over 500 lines are refused, so are re-reads of files that have"
  echo "        not changed, and past $(( CTX_TOKENS * ${CLAUDE_GEMMA_STOP_PCT:-80} / 100 )) tokens (${CLAUDE_GEMMA_STOP_PCT:-80}%) anything bulky is refused with"
  if (( LIFTABLE )); then
    echo "        an instruction to record findings and stop. touch /tmp/cc-guard-off to lift it"
    echo "        mid-session, which this session honours because you asked for --liftable."
  else
    echo "        an instruction to record findings and stop. Relaunch --liftable if you want"
    echo "        touch /tmp/cc-guard-off to work: without it the file is ignored, because a"
    echo "        model can make files and one did."
  fi
else
  echo "guard:  off. Unbounded reads and window overruns are permitted; a single task can"
  echo "        fill the window, and nothing here compacts by itself."
fi
if (( DEPTH )); then
  echo "depth:  gated as '$DEPTH_ADAPTER'. The contract goes in with your first prompt; an"
  echo "        answer whose citations do not survive re-reading is refused once, with every"
  echo "        gap in one message. touch /tmp/cc-depth-off to lift."
fi
echo "note:   the model and window above apply to new conversations only -- resuming one"
echo "        keeps whatever it was created with, whatever this banner says"
echo "limits: ${CLAUDE_GEMMA_MAX_OUTPUT:-8192} output tokens, $(( ${CLAUDE_GEMMA_TIMEOUT_MS:-1800000} / 60000 )) min request timeout"

# Compaction never happens by itself on this setup, and the declared window above does not
# change that; it only makes the percentage in the status line honest. Two independent paths
# exist in the client and both are closed here. The threshold path needs Je("tengu_sepia_moth")
# to be true, a remote feature gate defaulting to false that is never fetched, because gate
# fetching needs an Anthropic credential this machine does not have -- verified by removing
# CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC and finding cachedGrowthBookFeatures still absent.
# The reactive path fires only when the API reports "prompt is too long", which Ollama never
# does: it grows the KV cache instead. Both compactions in this project's history were manual,
# at 109,754 and 116,875 tokens, and the second cost 311s of which 244s was generation at
# 4.4 tok/s. Compacting near 75% costs a fraction of that.
echo "compact: manual only. Auto-compaction cannot work here (no account, so the feature"
echo "         gate stays false; and Ollama never reports an oversized prompt). The status"
echo "         line shows the count -- /compact when it asks at 60%, which is where the"
echo "         cost stops being 187s and starts being 311-666s. Red at 75% is late."
echo "thinking: summaries on (showThinkingSummaries). ctrl+o expands the transcript;"
echo "          Option+T (macOS) / Alt+T toggles thinking. Prefill is silent in the UI —"
echo "          python3 .cursor/skills/ollama-watch/scripts/state.py while you wait."

# The specific reason to hesitate here, beyond the obvious one about unattended shell
# commands: the property that makes this model safe to accept edits from is unverified on
# this exact path. Without a system prompt the 31B scores 0/20 on confidently-worded bug
# reports that are wrong about the code -- it patches whatever it was told to patch. With
# prompts/skeptic_min.md it scores 20/20. But every one of those measurements used that
# file as the *entire* system prompt, and Claude Code appends it to several thousand tokens
# of its own. Nothing has confirmed 63 words still bind in that position. Permission
# prompts are currently the only thing standing between an unverified disposition and your
# working tree, and --yolo removes them.
TOOL_ARGS=()
if (( LEAN_TOOLS )); then
  KEPT="$UNUSED_TOOLS"
  # A flow runs its stages as subagents, so the tools that launch one and show its progress are not
  # unused here whatever the lean list says.
  # TaskOutput stays out: one call copies everything a stage has done so far into the orchestrator's
  # window -- 32,164 characters each in run 25 -- and the flow tells it the verdict anyway. A tool that
  # is offered gets called, and the refusal costs a turn.
  (( FLOWS )) && KEPT="${KEPT//Agent,/}" && KEPT="${KEPT//Task,/}" \
              && KEPT="${KEPT//TaskCreate,/}" && KEPT="${KEPT//TaskUpdate,/}" \
              && KEPT="${KEPT//TaskList,/}" && KEPT="${KEPT//TaskGet,/}" \
              && KEPT="${KEPT//TaskStop,/}"
  TOOL_ARGS=(--disallowed-tools "$KEPT")
fi

YOLO_ARGS=()
if (( YOLO )); then
  YOLO_ARGS=(--dangerously-skip-permissions)
  echo "yolo:   ON -- no permission checks. Edits, writes and shell commands run"
  echo "        unattended in $(pwd)."
  if (( ! ${#PROMPT_ARGS} )); then
    echo "        No skepticism prompt: this model patches false bug reports 20 times"
    echo "        out of 20 in that configuration. Consider --prompt."
  else
    echo "        Skepticism prompt is on, but its effect has never been measured with"
    echo "        Claude Code's own system prompt in front of it. Keep git clean."
  fi
fi

if ! curl -sf -o /dev/null --max-time 5 "$OLLAMA_URL/api/tags"; then
  echo "error: no Ollama at $OLLAMA_URL" >&2
  echo "       start the Ollama app, or run 'ollama serve'." >&2
  exit 1
fi

# Braces are mandatory here. In zsh "$MODEL:latest" applies the :l history modifier,
# lowercasing MODEL and leaving the literal "atest" -- so the name silently becomes
# gemma4-31b-coding-64katest and every lookup fails.
MODEL_TAGGED="$MODEL"
[[ "$MODEL_TAGGED" == *:* ]] || MODEL_TAGGED="${MODEL}:latest"
if ! ollama list 2>/dev/null | awk -v m="$MODEL_TAGGED" '$1 == m { f=1 } END { exit f ? 0 : 1 }'; then
  echo "error: model $MODEL is not installed." >&2
  echo "       build it with: ollama create $MODEL -f $ROOT/modelfiles/$MODEL.Modelfile" >&2
  exit 1
fi

# Two of these do not fit at once. Evict the others before loading this one, so the
# shortfall surfaces here rather than as a stalled first request.
for other in gemma4-31b-coding-64k gemma4-31b-coding-96k gemma4-31b-coding-128k \
             gemma4-31b-mtp-64k gemma4-31b-mtp-96k gemma4-31b-mtp-128k \
             gemma4-31b-mlx-64k gemma4-31b-mlx-96k gemma4-31b-mlx-128k \
             gemma4-26b-coding-64k gemma4-26b-coding-96k gemma4-26b-coding-128k \
             gemma4-coding:31b gemma4-coding:31b-mtp gemma4-coding:31b-mlxbf16 \
             gemma4-coding:31b-mlx gemma4-coding:31b-q8 gemma4-coding:31b-qat \
             gemma4-coding:26b-a4b \
             gemma4:31b-it-bf16 gemma4:26b-a4b-it-bf16 gemma4:31b-mlx-bf16; do
  [[ "$other" == "$MODEL" ]] && continue
  ollama stop "$other" 2>/dev/null || true
done

# A second session against the same endpoint has a different prefix, and a large competing
# prefix evicts the first, leaving it to reprocess its whole prompt on its next turn.
# Measured at 33-60k prompts that is 40-70 seconds per switch, which reads as "the model is
# slow" rather than "I have two sessions open". Warn rather than refuse: a second session is
# sometimes worth the price.
# Counting processes that quote the settings blob does not work. One session is several
# processes -- a thin `claude` client, an app wrapper and a versioned runtime -- and only
# some of them carry the blob on their command line; the rest inherit the configuration
# through the environment, where ps cannot see it. Counting matches therefore mixes clients
# with runtimes and lands on a number that resembles a session count by luck: two real
# sessions were once counted as two, from one runtime plus one unrelated client.
# Every process belonging to a session does carry --session-id, so count the distinct ids
# instead. Undercounting is the safe direction: a missed warning costs a prompt reprocess,
# a false one sends you hunting for a session that does not exist.
OTHER_SESSIONS=$(ps -Eww -o command 2>/dev/null \
  | awk '/--session-id/ \
         && !/bg-spare|bg-pty-host|claude daemon/ \
         && !/awk|grep|rg / {
           for (i = 1; i <= NF; i++)
             if ($i == "--session-id" && (i + 1) <= NF && !($(i + 1) in seen)) {
               seen[$(i + 1)] = 1
               n++
             }
         } END { print n+0 }')
if (( OTHER_SESSIONS > 0 )); then
  echo
  echo "  warning: $OTHER_SESSIONS other Claude Code session(s) are already using this"
  echo "           endpoint. They share one prefix-cache slot, so each switch between"
  echo "           them costs a full prompt reprocess. Close them for full speed."
fi

# A daemon outlives the session that spawned it, and the next session attaches to whichever one
# is already running rather than starting its own. It therefore keeps the ANTHROPIC_* values it
# was born with, and exporting them here cannot reach it, because it is not our child.
#
# Measured on 2026-07-30: a daemon started eighteen hours earlier under 128k pulled in 62 GB of
# the 128k model for a session configured for 96k and pinned it for the whole keep-alive, while
# the 96k model the session actually named was never resident at all. Every turn therefore paid
# a cold load, and the reduction to 96k that the session was launched for never took effect.
# The bg-spare workers were worse still: theirs named a Qwen model from two configurations back.
#
# macOS hides the environment of signed system binaries but not of a user-installed node build,
# which is what claude is, so ps can read this. Should that ever change, the extraction returns
# nothing and the helper is reported as unreadable rather than killed on a guess.
typeset -a STALE_HELPERS OPAQUE_HELPERS
for pid in ${(f)"$(pgrep -f 'claude (daemon|bg-spare|bg-pty-host)' 2>/dev/null)"}; do
  [[ -z "$pid" ]] && continue
  helper_model=$(ps -Ewwp "$pid" 2>/dev/null | tr ' ' '\n' \
    | awk -F= '/^ANTHROPIC_(DEFAULT_)?MODEL=/ { print $2; exit }')
  if [[ -z "$helper_model" ]]; then
    OPAQUE_HELPERS+=("$pid")
  elif [[ "$helper_model" != "$MODEL" ]]; then
    STALE_HELPERS+=("$pid:$helper_model")
  fi
done

if (( ${#OPAQUE_HELPERS} )); then
  echo
  echo "  note: could not read the configuration of ${#OPAQUE_HELPERS} Claude Code helper(s)"
  echo "        (pid ${OPAQUE_HELPERS}). If the wrong model keeps loading, kill them by hand."
fi

if (( ${#STALE_HELPERS} )); then
  echo
  echo "  Claude Code helpers from an earlier launch are configured for another model:"
  for entry in $STALE_HELPERS; do
    echo "    pid ${entry%%:*} -> ${entry#*:}"
  done
  if (( OTHER_SESSIONS > 0 )); then
    echo "  refusing to launch: killing them would take down $OTHER_SESSIONS live session(s),"
    echo "  and leaving them means this session's model may never become resident."
    echo "  Close those sessions, then run this again."
    exit 1
  fi
  echo "  nothing live depends on them, so clearing them now; the daemon that replaces them"
  echo "  inherits this launch's settings."
  for entry in $STALE_HELPERS; do kill -TERM "${entry%%:*}" 2>/dev/null || true; done
  sleep 2
  for entry in $STALE_HELPERS; do kill -KILL "${entry%%:*}" 2>/dev/null || true; done
fi

echo "loading (a couple of minutes for a cold 60 GB model)..."
WARM_START=$SECONDS
# No messages array, which makes this a load-only request: Ollama returns done_reason=load,
# sets keep_alive and creates no prefix cache entry at all. An earlier version sent a real
# "ok" turn, which cached a 17-token prefix competing with whatever conversation was warm,
# for no purpose -- the launcher exists to keep that cache hot, not to add entries to it.
# It deliberately does not send keep_alive either. Doing so bought 8 hours here and nothing
# afterwards, because Claude Code's own turns omit the field and reset the expiry to the
# server default; the effect was a launcher that looked correct while every real request
# undid it. Relying on the default instead means what is verified below is what the session
# will actually get.
curl -sf -o /dev/null --max-time 600 -X POST "$OLLAMA_URL/api/chat" \
  -H 'content-type: application/json' \
  -d "{\"model\":\"$MODEL\"}" \
  || { echo "error: failed to load $MODEL" >&2; exit 1; }
echo "ready in $((SECONDS - WARM_START))s"

# Five idle minutes would otherwise unload 62 GB and cost a cold load plus a full prefill of
# the conversation -- about four minutes at 115k tokens. Worth one HTTP call to catch.
KEEP_MIN=$(curl -sf --max-time 10 "$OLLAMA_URL/api/ps" 2>/dev/null | python3 -c '
import json, sys
from datetime import datetime, timezone

try:
    models = json.load(sys.stdin).get("models") or []
except Exception:
    print(-1)
    sys.exit()
best = -1
for m in models:
    raw = (m.get("expires_at") or "").replace("Z", "+00:00")
    try:
        left = (datetime.fromisoformat(raw) - datetime.now(timezone.utc)).total_seconds()
    except ValueError:
        continue
    best = max(best, left / 60)
print(int(best))
' 2>/dev/null || echo -1)

if [[ "$KEEP_MIN" =~ ^-?[0-9]+$ ]] && (( KEEP_MIN >= 0 )) && (( KEEP_MIN < 60 )); then
  echo
  echo "  warning: Ollama will unload this model in ${KEEP_MIN} min of idle time, and the"
  echo "           next turn then pays a cold load plus a full prefill. Fix it once with:"
  echo "               scripts/ollama-keepalive.sh 8h"
fi

# The guard is registered only for the tools that can add bulk. Write and Edit are deliberately not
# matched: the hook always allows them, and matching them would spawn a process per edit for nothing.
# A missing script is a hard error rather than a silent downgrade, for the same reason the prompt
# files are: this launcher once ran for days claiming a prompt it was not sending.
typeset -a HOOK_EVENTS
if (( GUARD )); then
  GUARD_SCRIPT="$ROOT/scripts/cc-context-guard.py"
  if [[ ! -x "$GUARD_SCRIPT" ]]; then
    echo "error: context guard missing or not executable: $GUARD_SCRIPT" >&2
    echo "       launch with --no-guard to proceed without it" >&2
    exit 1
  fi
  # The declared window, not the real one: the client refuses to send past what it was told, so that
  # is the ceiling a session actually hits, and it is the lower of the two.
  GUARD_CMD="$GUARD_SCRIPT --window $CTX_DECLARED --framing $CTX_RESERVE"
  GUARD_CMD="$GUARD_CMD --stop-pct ${CLAUDE_GEMMA_STOP_PCT:-80}"
  PRE_TOOL+=("{ \"matcher\": \"Read|Bash|WebFetch|WebSearch|Write|Edit|MultiEdit\", \"hooks\": [ { \"type\": \"command\", \"command\": \"$GUARD_CMD\" } ] }")
fi

# The depth gate and the contract that makes it fair. Registering the gate without the contract
# would refuse answers against a shape the model was never told about, so the two are one switch.
# SubagentStop is included because a subagent that closes early hands its parent a confident
# summary, which is the same failure one level down and harder to see.
if (( DEPTH )); then
  DEPTH_GATE="$ROOT/scripts/cc-depth-gate.py"
  DEPTH_CONTRACT="$ROOT/scripts/cc-depth-contract.py --adapter $DEPTH_ADAPTER"
  if [[ ! -x "$ROOT/scripts/cc-depth-gate.py" || ! -x "$ROOT/scripts/cc-depth-contract.py" ]]; then
    echo "error: depth hooks missing or not executable in $ROOT/scripts" >&2
    echo "       launch without --depth to proceed" >&2
    exit 1
  fi
  HOOK_EVENTS+=("\"SessionStart\": [ { \"hooks\": [ { \"type\": \"command\", \"command\": \"$DEPTH_CONTRACT\" } ] } ]")
  HOOK_EVENTS+=("\"UserPromptSubmit\": [ { \"hooks\": [ { \"type\": \"command\", \"command\": \"$DEPTH_CONTRACT\" } ] } ]")
  HOOK_EVENTS+=("\"Stop\": [ { \"hooks\": [ { \"type\": \"command\", \"command\": \"$DEPTH_GATE\" } ] } ]")
  HOOK_EVENTS+=("\"SubagentStop\": [ { \"hooks\": [ { \"type\": \"command\", \"command\": \"$DEPTH_GATE\" } ] } ]")
fi

# The stage loop, for a session that runs its stages as subagents so you can watch them work. The
# scripted driver owns that loop and can simply stop; here the launches are made by the model, which
# is the thing being held to a standard, so the ordering is enforced by a hook instead: a stage is
# admitted only if it is the next one and nothing blocking has been refused. It needs the Task tool,
# which the lean tool list removes, so asking for flows puts it back.
if (( FLOWS )); then
  FLOW_GUARD="$ROOT/scripts/cc-flow-guard.py"
  if [[ ! -x "$FLOW_GUARD" ]]; then
    echo "error: flow guard missing or not executable: $FLOW_GUARD" >&2
    exit 1
  fi
  # No matcher: while a flow is running, a tool call that does a stage's work outside a stage is
  # refused, and that is not a question about which tool it was.
  # First, so that its refusals are the ones the model reads. Behind the context guard, the call
  # budget was invisible: run 12 spent 280 calls against a budget of 140 and never once saw the
  # message telling it to stop, because a refusal from the hook ahead of it got there first.
  PRE_TOOL=("{ \"hooks\": [ { \"type\": \"command\", \"command\": \"$FLOW_GUARD\" } ] }" ${PRE_TOOL[@]+"${PRE_TOOL[@]}"})
  # And afterwards, because a launch this hook permits can still be refused by the client -- which
  # left the flow holding a stage that never existed, and refusing every retry as a duplicate.
  HOOK_EVENTS+=("\"PostToolUse\": [ { \"matcher\": \"Task|Agent|TaskList|TaskOutput\", \"hooks\": [ { \"type\": \"command\", \"command\": \"$FLOW_GUARD\" } ] } ]")
fi

# One PreToolUse key holding every matcher. Two keys of the same name in the same object is not two
# hooks, it is the second one silently replacing the first.
(( ${#PRE_TOOL} )) && HOOK_EVENTS+=("\"PreToolUse\": [ ${(j:, :)PRE_TOOL} ]")

GUARD_JSON=""
(( ${#HOOK_EVENTS} )) && GUARD_JSON="  \"hooks\": { ${(j:, :)HOOK_EVENTS} },"

# enforceAvailableModels is set in the user's global settings and would reject a model
# that is not on its list. Rather than editing that file, this session supplies its own.
#
# showThinkingSummaries costs nothing and is worth having on. The reasoning is generated and echoed
# back regardless of whether it is displayed -- 33 blocks and 5,828 tokens in one measured session,
# paid on every subsequent turn -- so hiding it buys no context back, it only hides what you are
# already being charged for. Without this key the client requests no display mode and renders the
# blocks collapsed; with it, requests carry display "summarized" (verified in a pty capture) and the
# text appears in the conversation. Ollama ignores the field and returns full reasoning either way.
# ctrl+o shows the same content in the transcript view without any setting at all, and alt+t is the
# separate toggle that turns thinking off entirely.
SESSION_SETTINGS=$(cat <<JSON
{
  "env": {
    "ANTHROPIC_BASE_URL": "$OLLAMA_URL",
    "ANTHROPIC_AUTH_TOKEN": "ollama",
    "ANTHROPIC_API_KEY": "",
    "ANTHROPIC_MODEL": "$MODEL",
    "ANTHROPIC_DEFAULT_MODEL": "$MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "$MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "$MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "$SMALL_SLOT",
    "ANTHROPIC_SMALL_FAST_MODEL": "$SMALL_SLOT",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_CODE_ENABLE_AWAY_SUMMARY": "0",
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "$CTX_DECLARED",
    "API_TIMEOUT_MS": "${CLAUDE_GEMMA_TIMEOUT_MS:-1800000}",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "${CLAUDE_GEMMA_MAX_OUTPUT:-8192}",
    "CC_GUARD_LIFTABLE": "$LIFTABLE"
  },
$GUARD_JSON
  "model": "$MODEL",
  "availableModels": ["$MODEL", "$SMALL_SLOT"],
  "enforceAvailableModels": false,
  "showThinkingSummaries": true,
  "statusLine": { "type": "command", "command": "$ROOT/scripts/cc-statusline.py" }
}
JSON
)

if [[ "$PRINT_SETTINGS" == "1" ]]; then
  echo "$SESSION_SETTINGS"
  exit 0
fi

echo

# --settings governs this session, but ~/.claude/settings.json carries its own env block and
# Claude Code exports that into the processes it spawns. Those workers -- the daemon's
# bg-spare pool and whatever issues the periodic auxiliary call -- then read the environment
# rather than this session's settings. With a stale global block that meant they talked to a
# different model on a different port entirely, which is why the small-model routing never
# fired and why a logging proxy saw no auxiliary traffic. Exporting here wins, because a
# child inherits the environment before it ever reads a settings file.
export ANTHROPIC_BASE_URL="$OLLAMA_URL"
export ANTHROPIC_AUTH_TOKEN="ollama"
export ANTHROPIC_API_KEY=""
export ANTHROPIC_MODEL="$MODEL"
export ANTHROPIC_DEFAULT_MODEL="$MODEL"
export ANTHROPIC_DEFAULT_SONNET_MODEL="$MODEL"
export ANTHROPIC_DEFAULT_OPUS_MODEL="$MODEL"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="$SMALL_SLOT"
export ANTHROPIC_SMALL_FAST_MODEL="$SMALL_SLOT"
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1"
export CLAUDE_CODE_MAX_CONTEXT_TOKENS="$CTX_DECLARED"

# Claude Code abandons a request after 5 minutes when API_TIMEOUT_MS is unset, then resends
# it. Against a cloud model that is a generous ceiling; here it is below the median turn.
# Measured on one session: nine turns took 2m17s, 6m19s, 5m48s, 8m24s, 4m52s, 3m18s, 5m51s,
# 2m5s and 6m59s, and the retries landed 300 and 295 seconds after their originals, to the
# second. Worse, a resend queues behind the original, which is still generating, so the
# model does the work twice and the client abandons both. Eight messages of progress in
# forty minutes of continuous GPU time.
export API_TIMEOUT_MS="${CLAUDE_GEMMA_TIMEOUT_MS:-1800000}"

# The other half of the same problem. Claude Code asks for up to 32000 output tokens, which
# at ~13 tok/s on the draft checkpoint permits a 40-minute answer: the model does not have
# to misbehave to blow any timeout, only to be thorough. Capping it trades the tail of very
# long single responses for turns that finish. Raise it if a large edit ever comes back cut
# off mid-hunk.
export CLAUDE_CODE_MAX_OUTPUT_TOKENS="${CLAUDE_GEMMA_MAX_OUTPUT:-8192}"

# The away summary re-sends the whole conversation against the main model with its own prompt
# shape when you step away and return. That prefix displaces the conversation's, so the next
# real turn matches only the small head both share and reprocesses everything after it: one
# observed instance dropped reuse from 88.7% to 27.5% and cost 108s for a 40-word recap.
# Unlike the titling call it cannot be routed to $SMALL_SLOT, since it needs the transcript.
# Despite the ENABLE_ name the feature is on by default; "0" is checked before the remote gate.
export CLAUDE_CODE_ENABLE_AWAY_SUMMARY="0"

exec claude --model "$MODEL" --settings "$SESSION_SETTINGS" \
  "${TOOL_ARGS[@]}" "${PROMPT_ARGS[@]}" "${YOLO_ARGS[@]}" "${CLAUDE_ARGS[@]}"
