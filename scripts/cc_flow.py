"""What the stages are, so the scripted driver and the interactive session cannot disagree.

The same three-stance flows are run two ways here: `depth_pipeline.py` drives them as separate
`claude -p` calls, and a Claude Code session drives them as native subagents so their progress is
visible while they work. Those are two engines, not two designs, and the moment the stances live in
both files they start to drift. Earlier today a stage loop kept in two places let a refused plan be
implemented anyway, with a passing test for exactly that case sitting in the copy nobody ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field


STAGE_TOOLS = "Read,Grep,Glob,Bash"
EDITING_TOOLS = STAGE_TOOLS + ",Edit,Write,MultiEdit"


@dataclass
class Stage:
    """One pass over the problem. Stances differ; the engine and the contract do not."""

    name: str
    stance: str
    produces: str
    consumes: tuple[str, ...] = ()
    verify: bool = True
    writes: bool = False
    # A stage may be judged on a different contract from the run's. The plan of a change cannot be
    # held to red/green -- nothing has run yet -- but it can be held to naming the failure it
    # expects, which is where the first implement run actually went wrong.
    adapter: str | None = None
    # Whether the stages after this one are worth running if this one was refused. A plan that
    # committed to nothing was still implemented, faithfully, by the two stages below it -- which is
    # how a change that alters no behaviour gets built and then verified.
    blocking: bool = False

    @property
    def tools(self) -> str:
        return EDITING_TOOLS if self.writes else STAGE_TOOLS


DEFAULT_STAGES = [
    Stage(
        name="survey",
        produces="survey.md",
        verify=False,     # an inventory makes no claims, so there is nothing to verify yet
        stance=("Map the territory and stop. List the files, entry points and data that bear on "
                "the question, each with the line range you actually opened. A file here can be "
                "longer than one read allows, so say which part of it you saw and let the next "
                "stage search for the rest. Draw no conclusions and name no defects -- a later "
                "stage does that, and anything you assert here it will have to re-derive. If "
                "something looks wrong, note the location only."),
    ),
    Stage(
        name="claims",
        produces="claims.md",
        consumes=("survey.md",),
        # Nothing downstream means anything without this. Run 7 gave up on claims and then ran the
        # adversary anyway, which had nothing to attack, said so, and was refused for saying it.
        blocking=True,
        stance=("Now make the claims the survey supports, and only those. Open every file you "
                "cite, in this stage, before quoting it -- the survey is a map, not a substitute "
                "for reading, and it saw at most the first part of a long file. To cite a line "
                "beyond that, find it with a search and read around the hit; a line number you "
                "have not seen is a guess even when the claim is right. Each claim gets its own "
                "block. If the survey pointed somewhere you could not resolve, that is an "
                "UNKNOWN, not a guess.\n"
                "A claim about what a program does when run is settled by running it, and a hook "
                "is a program: feed it the JSON it expects on stdin and report what it printed, "
                "`echo '{\"tool_name\": \"Bash\", \"tool_input\": {\"command\": \"...\"}}' "
                "| python3 <the hook> `. That is evidence. Describing what it would print is not, "
                "however obvious the reasoning looks, and a claim that a rule can be walked around "
                "is the one most worth actually walking around -- against the hook itself, not "
                "against the machine, so nothing is left behind."),
    ),
    Stage(
        name="adversary",
        produces="verdict.md",
        consumes=("claims.md",),
        stance=("Try to break each claim above. For each one, run the cheapest thing that would "
                "show it false and report what it printed -- a rule in a hook is attacked by "
                "feeding the hook the payload that should trip it and showing what it decided; a "
                "claim your attack kills is deleted, and a claim you cannot test becomes an "
                "UNKNOWN with the reason. Do not add new findings. Do not soften the surviving "
                "ones -- restate them with their evidence intact."),
    ),
]


# The implement flow. Same engine, three stances, and only the middle one may write. The split is
# not tidiness: a session that reads, edits and then judges its own edit has no state left that it
# did not produce, which is the exact condition the gate cannot check anything under.
IMPLEMENT_STAGES = [
    Stage(
        name="plan",
        produces="plan.md",
        adapter="change-plan",
        blocking=True,
        stance=("Find the code the task names and stop. Report the file and line range you opened, "
                "the behaviour as it stands, and the single smallest test that would fail because "
                "of it -- where that test goes, what it asserts, and the exact command that would "
                "run it, and the string that command will print while the defect is there. Write "
                "no code and change nothing. If the task's premise does not survive "
                "reading the code, say so and stop; that is a complete and useful answer."),
    ),
    Stage(
        name="implement",
        produces="change.md",
        consumes=("plan.md",),
        verify=False,     # judged in the next stage, on a tree it can no longer touch
        writes=True,
        stance=("Do it, in this order, and report each step with what it printed. Write the test "
                "from the plan and run it: it must fail, and the failure must be about the "
                "behaviour, not an import or a typo. Then change the source until that same "
                "command passes -- same command, not a narrower one. Then run the suite around it. "
                "Change nothing the task did not ask for; a tidy-up in the same diff makes the "
                "next stage unable to attribute either. If the test cannot be made to fail first, "
                "stop and say why rather than writing a test that passes on both sides."),
    ),
    Stage(
        name="verify",
        produces="verdict.md",
        consumes=("plan.md", "change.md"),
        stance=("Prove the change is load-bearing by taking it away. Stash the source edit and "
                "keep the test -- `git stash push -- <the source files, not the test>` -- run the "
                "exact command from the previous stage and show it failing, then `git stash pop` "
                "and run that same command again and show it passing. Then run the suite and cite "
                "its counts. Report both runs as command evidence. If the test passes with the "
                "change stashed, the change is not what made it pass, and that is the finding."),
    ),
]

# Flows by the name a person types. The order is the order they run in.
FLOWS = {
    "review": DEFAULT_STAGES,
    "implement": IMPLEMENT_STAGES,
}

# The adapter a flow's stages are judged under when a stage does not name its own.
FLOW_ADAPTER = {"review": "review", "implement": "implement"}


def flow_for(name: str):
    return FLOWS.get((name or "").strip().lower())


def stage_in(flow: str, stage: str):
    for candidate in flow_for(flow) or ():
        if candidate.name == stage:
            return candidate
    return None


def adapter_for(flow: str, stage: str) -> str:
    found = stage_in(flow, stage)
    if found is None:
        return FLOW_ADAPTER.get(flow, "review")
    return found.adapter or FLOW_ADAPTER.get(flow, "review")
