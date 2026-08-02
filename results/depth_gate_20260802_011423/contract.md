TASK CONTRACT (review). Claims about defects in code that already exists.

This session is gated: an answer is refused unless its claims carry evidence that can be
looked up. State each finding as a block, and nothing else in the final answer:

CLAIM: <one sentence>
EVIDENCE: <path>:<first_line>-<last_line>
QUOTE:
<the exact lines, copied from the file as you read it>

For a claim you would call high severity, add both lines:
SEVERITY: high
FALSIFICATION: <the command you ran to try to disprove it, and what it printed>

Anything you could not establish goes at the end as `UNKNOWN: <one sentence>`.
Saying you did not check is always allowed and never penalised. Guessing is not.

Stance for this task:
- Every defect claim quotes the code it is about.
- A high-severity defect needs a probe that reproduces it, or it is not high.
- No defect found is a legal and complete answer.
