# Claims about the long-sleep rule in cc-context-guard.py

## Claim 1: The 30-second threshold mischaracterizes "minutes"
**What it asserts**: The commit message says "a long sleep is always the wrong answer rather than sometimes" and gives 180-second examples, but the actual threshold is 30 seconds. The denial message says "Do not sleep for Xs" and the framing is about "minutes", yet the guard fires at 31 seconds.
**Verdict**: TRUE — the rule is stricter than the justification warrants. The commit's reasoning is about 180-second sleeps ("minutes"), but the guard's threshold is 30 seconds. A 31-second sleep is not "minutes". This is a real mismatch: the guard's behavior is more aggressive than the evidence supports.

## Claim 2: A stage may legitimately need to wait 31–60 seconds
**What it asserts**: There exist realistic scenarios where a stage needs to wait for something that takes 31–60 seconds, making the 30-second threshold too aggressive.
**Verdict**: TRUE. Examples:
- A test suite that takes ~45 seconds to run (common for integration tests, e2e tests, or large unit suites)
- A build step that takes 30–45 seconds
- A slow command that genuinely takes 31–60 seconds
- A CI/CD pipeline step that has a known duration
While the commit's specific example (180 seconds for a 3-second task) is clearly wrong, the leap from "180 seconds is wrong" to "31 seconds is also wrong" is not justified. The 30-second threshold is arbitrary.

## Claim 3: The regex catches the right thing but has a gap with variables
**What it asserts**: `_SLEEP = re.compile(r"\bsleep\s+(\d+)")` correctly catches literal numeric sleeps but misses variable-based sleeps.
**Verdict**: TRUE. The regex catches `sleep 180`, `sleep 31`, `sleep 10 && sleep 2` (it finds both 10 and 2). It does NOT catch:
- `sleep $DURATION` (variable)
- `sleep $(echo 180)` (substitution)
- `sleep $((30 + 1))` (arithmetic)
This gap means the rule is not as comprehensive as it appears to be.

## Claim 4: The reasoning about foreground vs background is sound for the specific case but overgeneralized
**What it asserts**: "Run the command in the foreground and wait for it there: polling a background job costs the whole wait and tells you nothing the exit status would not."
**Verdict**: PARTLY TRUE. For the specific case (pytest in background, sleep 180, tail), the reasoning is correct: running pytest in the foreground and waiting is the right approach. However, the generalization that "polling a background job costs the whole wait and tells you nothing the exit status would not" is too broad. There are cases where:
- You need to check intermediate progress (tail of a log file)
- The command has a very long tail that you want to monitor
- You're waiting for something else to complete while the command runs
In these cases, the foreground-only approach loses visibility.

## Claim 5: "Always the wrong answer rather than sometimes" is false
**What it asserts**: The commit's claim that a long sleep is "always the wrong answer rather than sometimes" is an overstatement.
**Verdict**: FALSE. "Always" is too strong. While 180-second sleeps for 3-second tasks are wrong, there are scenarios where a 31–60 second sleep IS the right thing to do:
- Waiting for a slow external service to be ready
- Waiting for a file to be written by another process
- Waiting for a database migration to complete
- Waiting for a build step that genuinely takes 45 seconds
The rule conflates "always wrong" with "usually wrong" and makes an incorrect universal claim.

## Claim 6: The denial message's escape hatch is inadequate
**What it asserts**: "If something really does take minutes, say so and answer without it" doesn't help when the wait is 31–60 seconds.
**Verdict**: TRUE. The escape hatch says "if something really does take minutes" — but the guard fires at 31 seconds. A stage that needs to wait 45 seconds for a real reason can't use this escape because 45 seconds is not "minutes" either. The escape hatch doesn't cover the range where the guard actually fires.

## Claim 7: The guard provides no way for a stage with a legitimate need to proceed
**What it asserts**: There is no path for a stage that legitimately needs to wait 31+ seconds to proceed without manually editing the code or using the off-switch.
**Verdict**: TRUE. The only escape hatches are:
1. `touch /tmp/cc-guard-off` (manual, session-wide, destructive)
2. Wait under 30 seconds (may not be enough)
3. Run the command in the foreground (may not work if you need to monitor progress)
4. Answer without waiting (may lose correctness)
There's no mechanism like `--max-sleep N` for a specific stage, or an `--explain` flag to justify a longer wait.
