# Adversary: Refuting the claims about the long-sleep rule

## Claim 1: The 30-second threshold mischaracterizes "minutes"
**Refutation**: The 30-second threshold is not about characterizing minutes — it's about catching the pattern. The commit's trigger was a 180-second sleep for a 3-second task. A 30-second threshold is a reasonable heuristic for "this is polling rather than running directly." The claim treats the guard as if it needs to be mathematically precise about what "minutes" means, but it's a heuristic guard, not a precise policy. The mismatch between the commit message's language and the code's threshold is a documentation issue, not a correctness issue.

## Claim 2: A stage may legitimately need to wait 31–60 seconds
**Refutation**: The claim doesn't provide a concrete example. "A test suite that takes ~45 seconds" — why would a test suite require a background + sleep pattern? It should run in the foreground. "A build step that takes 30–45 seconds" — same. "A slow command that genuinely takes 31–60 seconds" — run it in the foreground. The claim is vague because the examples don't actually support the claim: a 45-second command doesn't need a sleep to wait for it; you just run it and wait.

## Claim 3: The regex has a gap with variables
**Refutation**: This is a real gap, but it's minor. If someone writes `sleep $DURATION`, the variable will expand to some value, and if that value is >30, the guard misses it. However, this is a minor gap in a heuristic. The guard catches the obvious cases. A variable-based sleep is less common than literal sleeps, and the guard still catches `sleep 31` and `sleep 32`. The claim treats this as a fundamental flaw, but it's a minor imperfection in a heuristic.

## Claim 4: The reasoning about foreground vs background is overgeneralized
**Refutation**: The claim says "there are cases where you need to check intermediate progress" but doesn't provide a concrete example. For a stage that's judging a change it didn't make, the question is: does the stage need to monitor progress of a command it didn't start? The answer is almost always no. If a command takes 31–60 seconds, you run it in the foreground and wait. The claim is speculative.

## Claim 5: "Always the wrong answer rather than sometimes" is false
**Refutation**: The claim conflates "31 seconds is not minutes" with "the claim is false." The commit says "Nothing here needs to wait minutes for anything." The guard fires at 31 seconds, which is not minutes. The claim is straw-manning the commit's actual claim. The commit's claim about minutes is true. The guard's behavior at 31 seconds is a reasonable extension of that claim: if nothing needs to wait minutes, nothing needs to wait 31 seconds either. The "always" refers to the pattern (sleeping to wait for something), not the specific number of seconds.

## Claim 6: The denial message's escape hatch is inadequate
**Refutation**: The claim says "45 seconds is not minutes either" but this is pedantic. The escape hatch says "If something really does take minutes, say so and answer without it." The guard fires at 31 seconds, which is below the "minutes" threshold. The escape hatch covers the real cases the guard was designed for. The claim is treating the escape hatch as if it should cover the exact range where the guard fires, but the escape hatch is for the cases the guard was designed for (long waits).

## Claim 7: The guard provides no way for a stage with a legitimate need to proceed
**Refutation**: The claim says "there is no mechanism like `--max-sleep N` for a specific stage, or an `--explain` flag." But this is a design choice, not a flaw. The guard's job is to prevent the bad pattern, not to accommodate it. The escape hatch `touch /tmp/cc-guard-off` is sufficient for edge cases. Creating escape hatches for bad patterns is a slippery slope: once you allow `--max-sleep N` for a specific stage, you undermine the guard's purpose.

## Claims that survive the adversary:

**Claim 2 (partial)**: While the claim doesn't provide a concrete example, the 30-second threshold is still arbitrary. The commit's evidence is about 180-second sleeps, and the guard fires at 31 seconds. There's a real gap between the evidence and the threshold. The claim is weakened by not providing a concrete example, but the underlying point — the threshold is more aggressive than the evidence warrants — is valid.

**Claim 3**: The regex gap with variables is real. If someone writes `sleep $DURATION`, the guard misses it. This is a minor gap, but it's a real gap.

**Claim 5 (partial)**: The "always" claim is overstated. The commit says "Nothing here needs to wait minutes for anything," and the guard fires at 31 seconds. The "always" is too strong because the evidence only covers 180-second sleeps, not 31-second sleeps. The claim survives in a weaker form: the "always" is overstated, even if the specific threshold is debatable.
