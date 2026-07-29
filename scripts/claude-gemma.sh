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

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OLLAMA_URL="${OLLAMA_HOST_URL:-http://127.0.0.1:11434}"
SKEPTIC_PROMPT="$ROOT/prompts/skeptic_min.md"
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
YOLO=0

usage() {
  cat <<'USAGE'
Usage: claude-gemma [31b|26b] [fast|accurate] [64k|128k|max] [mlx] [options] [-- claude args]

  31b            gemma4 31B, dense-attention. Stable and disciplined. (default)
  26b            gemma4 26B-A4B, sparse. Faster, but solves only half the repohard suite
                 deterministically and coin-flips the rest. Accurate weights only.

  fast           the draft-equipped checkpoint: speculative decoding, 2-3.4x faster
                 depending on context. Loses 10 audittrap points, 8 pyhard points and
                 vision; ties on repohard, claim, arch and trap discipline. (default)
  accurate       the plain dense model: best measured quality, accepts images, 8.1 tok/s.

  64k            65536 context, the size most scores were measured at. (default)
  128k           131072 context for large repositories. Quality there is unmeasured, and
                 the draft advantage is down to ~1.9x by 100k tokens.
  max            the model's full 262144. Largest window, least evidence behind it.

  mlx            Ollama's MLX runner instead of llama.cpp. Same weights as 'fast', within
                 5% on speed, and far less exercised. Kept for comparison, not advised.

  --prompt       Force the skepticism system prompt on (default: on for 31b only).
  --no-prompt    Force it off.
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
  claude-gemma -- --continue          resume the previous session
USAGE
}

CLAUDE_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    31b|31B) SIZE="31b"; shift ;;
    26b|26B) SIZE="26b"; shift ;;
    fast|draft|mtp) WEIGHTS="fast"; shift ;;
    accurate|dense|quality) WEIGHTS="accurate"; shift ;;
    64k|64K) CTX="64k"; shift ;;
    128k|128K) CTX="128k"; shift ;;
    max|MAX|native) CTX="max"; shift ;;
    mlx|MLX) RUNTIME="mlx"; shift ;;
    cpp|CPP|llamacpp) RUNTIME="cpp"; shift ;;
    --prompt) USE_PROMPT="on"; shift ;;
    --no-prompt) USE_PROMPT="off"; shift ;;
    --yolo|-y) YOLO=1; shift ;;
    --list)
      echo "Installed Gemma variants:"
      ollama list 2>/dev/null | awk 'NR==1 || /^gemma4-(31b|26b)-(coding|mtp|mlx)-|^gemma4-coding:/ { print "  " $0 }'
      exit 0 ;;
    -h|--help) usage; exit 0 ;;
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

if [[ "$USE_PROMPT" == "auto" ]]; then
  [[ "$SIZE" == "31b" ]] && USE_PROMPT="on" || USE_PROMPT="off"
fi

# Gemma emits LaTeX for arithmetic, a habit from its Gemini-family training, and a terminal
# renders none of it: "$55 \times \text{result size}$" arrives on screen exactly like that.
# Claude Code refuses --append-system-prompt together with --append-system-prompt-file, so
# the two are composed into a scratch file per launch. skeptic_min.md therefore stays
# byte-identical to the file the 20/20 trap result was measured against. Note the composed
# prompt is 28 words longer than anything measured, and on the 26B every added word has so
# far cost fix points.
FORMAT_RULE='Write arithmetic, formulas and units as plain text. Never use LaTeX, MathJax, or dollar-delimited math. Write "55 * result size", not "$55 \times \text{result size}$".'
COMPOSED_PROMPT="${TMPDIR:-/tmp}/claude-gemma-system-prompt.md"

PROMPT_ARGS=()
if [[ "$USE_PROMPT" == "on" && -f "$SKEPTIC_PROMPT" ]]; then
  { cat "$SKEPTIC_PROMPT"; printf '\n%s\n' "$FORMAT_RULE"; } > "$COMPOSED_PROMPT"
  PROMPT_ARGS=(--append-system-prompt-file "$COMPOSED_PROMPT")
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
  echo "prompt: $(basename "$SKEPTIC_PROMPT") + plain-text math rule"
else
  echo "prompt: none (raw model behaviour, including LaTeX arithmetic)"
fi
echo "small:  $SMALL_NOTE"

# The specific reason to hesitate here, beyond the obvious one about unattended shell
# commands: the property that makes this model safe to accept edits from is unverified on
# this exact path. Without a system prompt the 31B scores 0/20 on confidently-worded bug
# reports that are wrong about the code -- it patches whatever it was told to patch. With
# prompts/skeptic_min.md it scores 20/20. But every one of those measurements used that
# file as the *entire* system prompt, and Claude Code appends it to several thousand tokens
# of its own. Nothing has confirmed 63 words still bind in that position. Permission
# prompts are currently the only thing standing between an unverified disposition and your
# working tree, and --yolo removes them.
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
for other in gemma4-31b-coding-64k gemma4-31b-coding-128k \
             gemma4-31b-mtp-64k gemma4-31b-mtp-128k \
             gemma4-31b-mlx-64k gemma4-31b-mlx-128k \
             gemma4-26b-coding-64k gemma4-26b-coding-128k \
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
# Which process carries the settings varies: launched through this script they sit on the
# `claude --model ... --settings` wrapper, but other launch paths put them on the app binary
# instead, and the wrapper's children inherit them through the environment where ps cannot
# see them. So key off the settings blob rather than any one binary path, and exclude both
# the daemon's helpers and whatever inspection command happens to quote these strings.
OTHER_SESSIONS=$(ps -eo pid,command 2>/dev/null \
  | awk '/ANTHROPIC_BASE_URL/ \
         && !/bg-spare|bg-pty-host|claude daemon/ \
         && !/awk|grep|rg / { n++ } END { print n+0 }')
if (( OTHER_SESSIONS > 0 )); then
  echo
  echo "  warning: $OTHER_SESSIONS other Claude Code session(s) are already using this"
  echo "           endpoint. They share one prefix-cache slot, so each switch between"
  echo "           them costs a full prompt reprocess. Close them for full speed."
fi

echo "loading (a couple of minutes for a cold 60 GB model)..."
WARM_START=$SECONDS
# No messages array, which makes this a load-only request: Ollama returns done_reason=load,
# sets keep_alive and creates no prefix cache entry at all. An earlier version sent a real
# "ok" turn, which cached a 17-token prefix competing with whatever conversation was warm,
# for no purpose -- the launcher exists to keep that cache hot, not to add entries to it.
curl -sf -o /dev/null --max-time 600 -X POST "$OLLAMA_URL/api/chat" \
  -H 'content-type: application/json' \
  -d "{\"model\":\"$MODEL\",\"keep_alive\":\"8h\"}" \
  || { echo "error: failed to load $MODEL" >&2; exit 1; }
echo "ready in $((SECONDS - WARM_START))s"

# enforceAvailableModels is set in the user's global settings and would reject a model
# that is not on its list. Rather than editing that file, this session supplies its own.
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
    "CLAUDE_CODE_ENABLE_AWAY_SUMMARY": "0"
  },
  "model": "$MODEL",
  "availableModels": ["$MODEL", "$SMALL_SLOT"],
  "enforceAvailableModels": false
}
JSON
)

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

# The away summary re-sends the whole conversation against the main model with its own prompt
# shape when you step away and return. That prefix displaces the conversation's, so the next
# real turn matches only the small head both share and reprocesses everything after it: one
# observed instance dropped reuse from 88.7% to 27.5% and cost 108s for a 40-word recap.
# Unlike the titling call it cannot be routed to $SMALL_SLOT, since it needs the transcript.
# Despite the ENABLE_ name the feature is on by default; "0" is checked before the remote gate.
export CLAUDE_CODE_ENABLE_AWAY_SUMMARY="0"

exec claude --model "$MODEL" --settings "$SESSION_SETTINGS" \
  "${PROMPT_ARGS[@]}" "${YOLO_ARGS[@]}" "${CLAUDE_ARGS[@]}"
