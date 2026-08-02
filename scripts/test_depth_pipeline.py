#!/usr/bin/env python3
"""Offline checks for the stage driver: the model call is faked, so no GPU and no eviction.

What is worth testing without a model is everything that is not the model: that a stage hands its
artifact to the next one, that a thin answer is refused exactly once and the second answer is
re-checked, that the prompt head stays byte-identical across stages (the whole basis of the warm
fan-out), and that two drivers cannot run at once against a single runner.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import os
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

_spec = importlib.util.spec_from_file_location("depth_pipeline", REPO / "scripts/depth_pipeline.py")
dp = importlib.util.module_from_spec(_spec)
sys.modules["depth_pipeline"] = dp
_spec.loader.exec_module(dp)

import cc_ledger  # noqa: E402

SAMPLE = "def add(a, b):\n    return a + b\n"
THIN = "CLAIM: add is wrong.\nEVIDENCE: src/m.py:1-2\nQUOTE:\n    return a - b\n"
SOLID = "CLAIM: add returns the sum.\nEVIDENCE: src/m.py:1-2\nQUOTE:\ndef add(a, b):\n    return a + b\n"


class Fake:
    """Stands in for `claude -p`, recording what it was asked."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.resumed: list[bool] = []
        self.settings: list = []
        self.tools: list[str] = []
        self.disallowed: list[str] = []

    def __call__(self, prompt, model, head, session, cwd, settings, resume=False, yolo=False,
                 timeout=3600, tools=dp.STAGE_TOOLS, disallowed=""):
        self.prompts.append(prompt)
        self.disallowed.append(disallowed)
        self.resumed.append(resume)
        self.settings.append(settings)
        self.tools.append(tools)
        return (self.replies.pop(0) if self.replies else ""), ""


def _run(replies, stages="survey,claims", task="does add add?", adapter="review"):
    tmp = tempfile.mkdtemp()
    root = Path(tmp)
    (root / "src").mkdir()
    (root / "src/m.py").write_text(SAMPLE)
    out = root / "out"
    out.mkdir()
    fake = Fake(replies)
    original, dp.invoke = dp.invoke, fake
    dp.LOCK = root / "lock"
    try:
        head = dp.head_file(out)
        contract = cc_ledger.contract_for(adapter)
        chosen = [{s.name: s for s in dp.DEFAULT_STAGES + dp.IMPLEMENT_STAGES}[n]
                  for n in stages.split(",")]
        results = dp.run_flow(chosen, contract, task, "fake-model", head, out, root, False, False,
                              adapter, quiet=True)
        return results, fake, out, root
    finally:
        dp.invoke = original


def test_a_stage_is_told_where_to_put_its_scratch_files() -> None:
    """Six runs left twelve repro scripts in the repository root."""
    _, fake, out, _ = _run(["an inventory of src/m.py:1-2", SOLID])
    assert str(out / "scratch") in fake.prompts[0], fake.prompts[0][-400:]
    assert (out / "scratch").is_dir()


def test_every_turn_names_its_own_model_to_the_client() -> None:
    """A stale global availableModels list otherwise sends the request to a cloud model."""
    results, fake, out, _ = _run(["an inventory of src/m.py:1-2", SOLID])
    assert fake.settings, "no turn was made"
    for path in fake.settings:
        written = json.loads(Path(path).read_text())
        assert written["model"] == "fake-model"
        assert written["enforceAvailableModels"] is False
        assert written["availableModels"] == ["fake-model"]
        guard = written["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        assert "cc-context-guard.py" in guard, "an unattended stage with no guard reads until it "\
                                               "overruns the window"
        assert "--stop-advice answer" in guard, "a read-only stage cannot write NOTES.md"


def test_stage_output_reaches_the_next_stage() -> None:
    results, fake, out, _ = _run(["an inventory of src/m.py:1-2", SOLID])
    assert (out / "survey.md").read_text() == "an inventory of src/m.py:1-2"
    assert "an inventory of src/m.py:1-2" in fake.prompts[1], "claims stage must see the survey"
    assert results[1].claims == 1 and not results[1].gaps, results[1].gaps


def test_survey_stage_makes_no_claims_and_is_not_gated() -> None:
    results, fake, _, _ = _run(["prose with no blocks at all"], stages="survey")
    assert results[0].rounds == 1 and not results[0].gaps
    assert len(fake.prompts) == 1, "an inventory has nothing to refuse"


def test_thin_answer_is_refused_once_then_accepted() -> None:
    results, fake, out, _ = _run(["survey", THIN, SOLID])
    claims = results[1]
    assert claims.rounds == 2, "exactly one refusal"
    assert fake.resumed[2] is True, "the retry must resume the session, not start a new one"
    assert "not accepted yet" in fake.prompts[2], fake.prompts[2][:200]
    assert not claims.gaps, claims.gaps
    assert (out / "claims.md").read_text() == SOLID, "the corrected answer is what gets stored"


def test_a_still_thin_answer_is_recorded_not_refused_again() -> None:
    results, fake, out, _ = _run(["survey", THIN, THIN])
    assert results[1].rounds == 2 and len(fake.prompts) == 3, "never a third turn"
    assert results[1].gaps, "the surviving gaps must be reported, not hidden"
    report = json.loads((out / "claims.gate.json").read_text())
    assert report["gaps"], report


def test_prompt_head_is_identical_across_stages() -> None:
    _, _, out, _ = _run(["survey", SOLID])
    head = (out / "head.md").read_bytes()
    assert hashlib.sha256(head).hexdigest(), "head must exist"
    prompts = sorted(out.glob("*.prompt.txt"))
    assert len(prompts) == 2
    # The head is a separate file sent identically; the per-stage prompt is the divergent tail.
    assert all(not p.read_text().startswith(head.decode()) for p in prompts), \
        "the head must not be duplicated into the tail, or the shared prefix moves"


def test_lock_refuses_a_concurrent_run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "lock"
        lock.write_text("someone else")
        original, dp.LOCK = dp.LOCK, lock
        argv = sys.argv
        sys.argv = ["depth_pipeline.py", "task", "--out", str(Path(tmp) / "o"), "--cwd", tmp]
        try:
            assert dp.main() == 1, "a second driver must refuse: one runner, one variant"
        finally:
            dp.LOCK, sys.argv = original, argv


def test_coverage_is_not_asserted_without_a_transcript() -> None:
    """A missing transcript must not be reported as 'you never read that'."""
    results, _, out, _ = _run(["survey", SOLID])
    report = json.loads((out / "claims.gate.json").read_text())
    assert report["coverage_checked"] is False
    assert not any("no read in this session" in g for g in report["gaps"]), report["gaps"]


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  %s" % t.__name__)
    print("\n%d checks passed" % len(tests))
    return 0


if __name__ == "__main__":
    sys.exit(main())


# A plan the change-plan contract accepts: it cites what it read and names the failure it expects
# before any code exists. Without the PREDICT line the stage is refused, which is the point of it.
PLAN = ("CLAIM: add returns the sum, and the caller wants the product.\n"
        "EVIDENCE: src/m.py:1-2\n"
        "QUOTE:\ndef add(a, b):\n    return a + b\n"
        "PREDICT: command: pytest -q -> AssertionError: 5 != 6\n")


def test_only_the_implement_stage_is_handed_the_editing_tools() -> None:
    """The plan reads and the verify stage judges; a stage that could edit either would be judging
    a tree it had moved."""
    _, fake, _, _ = _run([PLAN, "changed it", "stashed it and it failed"],
                         stages="plan,implement,verify", adapter="implement")
    handed = dict(zip(["plan", "implement", "verify"], fake.tools))
    assert "Edit" not in handed["plan"] and "Write" not in handed["plan"], handed
    assert "Edit" in handed["implement"] and "Write" in handed["implement"], handed
    assert "Edit" not in handed["verify"] and "Write" not in handed["verify"], handed


def test_the_writing_stage_is_told_to_change_the_repository_not_scratch() -> None:
    """The read-only stages are told the opposite, and that instruction would send a fix to /tmp."""
    _, fake, out, _ = _run([PLAN, "changed it", "stashed it and it failed"],
                           stages="plan,implement,verify", adapter="implement")
    plan, implement = fake.prompts[0], fake.prompts[1]
    assert "Do not create files in the repository" in plan, plan[-300:]
    assert "Change the files the task requires" in implement, implement[-300:]
    assert str(out / "scratch") in implement, implement[-300:]



def test_the_plan_stage_is_judged_on_its_own_contract() -> None:
    """The run's adapter is implement, which the plan cannot satisfy and is not asked to.

    It is asked for something else: the failing run it expects, named before the code exists. The
    first real implement run went wrong precisely here, in the one stage nothing was checking.
    """
    results, _, _, _ = _run(["a plan with no commitment in it"], stages="plan", adapter="implement")
    gaps = " ".join(results[0].gaps)
    assert "commits to nothing" in gaps, gaps
    assert "failed and then passed" not in gaps, "the plan must not be held to the run's contract"


def test_a_plan_that_names_its_failure_passes() -> None:
    results, _, _, _ = _run([PLAN], stages="plan", adapter="implement")
    assert results[0].gaps == [], results[0].gaps


def test_the_prediction_reaches_the_stage_that_must_honour_it() -> None:
    """Read off plan.md rather than carried in memory, so a stage rerun alone judges the same."""
    _, _, out, _ = _run([PLAN], stages="plan", adapter="implement")
    import cc_verify
    assert [p["expect"] for p in cc_verify.predictions((out / "plan.md").read_text())] \
        == ["AssertionError: 5 != 6"]


def test_a_base_url_the_caller_set_is_not_overwritten(monkeypatch) -> None:
    """Pointing the pipeline at llama-server has to be possible from outside it."""
    import depth_pipeline as dp
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8099")
    seen = {}

    def fake(cmd, **kw):
        seen.update(kw.get("env") or {})
        raise SystemExit(0)

    monkeypatch.setattr(dp.subprocess, "run", fake)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            dp.invoke("p", "m", Path(tmp) / "h.md", "s", Path(tmp), Path(tmp) / "s.json", ())
        except SystemExit:
            pass
    assert seen.get("ANTHROPIC_BASE_URL") == "http://127.0.0.1:8099", seen.get("ANTHROPIC_BASE_URL")

def test_a_refused_plan_stops_the_flow() -> None:
    """The stages below a plan execute it faithfully, which is the problem.

    A plan that committed to nothing was implemented and then verified anyway, and what came
    out passed a real red/green pair while changing no behaviour at all.
    """
    results, fake, _, _ = _run(["a plan with no commitment in it", "changed it", "verified"],
                               stages="plan,implement,verify", adapter="implement")
    assert [r.stage for r in results] == ["plan"], [r.stage for r in results]
    assert len(fake.prompts) <= 2, fake.prompts


def test_a_plan_that_commits_lets_the_flow_continue() -> None:
    results, _, _, _ = _run([PLAN, "changed it", "verified"],
                            stages="plan,implement,verify", adapter="implement")
    assert [r.stage for r in results] == ["plan", "implement", "verify"], results

def test_no_cache_lines_is_not_a_measurement_of_zero() -> None:
    """Pointed at llama-server, nothing matches Ollama's log and the column read 0.0%.

    That is an absence of measurement printed as a measurement, in the one column that says
    whether the shared head is earning its keep.
    """
    assert dp.StageResult(stage="s", session="x").reuse is None
    assert dp.StageResult(stage="s", session="x", cache=[(100, 80)]).reuse == 80.0

def test_a_judging_stage_is_forbidden_the_writing_tools_not_merely_unoffered() -> None:
    """--allowed-tools pre-approves; it does not forbid, and --dangerously-skip-permissions
    forbids nothing at all. A verify stage carrying a read-only tool list made nine successful
    edits to the files it was judging, which is not a report on a change but a second author.
    """
    _, fake, out, _ = _run([PLAN, "changed it", "verified it"],
                           stages="plan,implement,verify", adapter="implement")
    kinds = dict(zip(["plan", "implement", "verify"], fake.disallowed))
    assert kinds["implement"] == "", kinds
    assert "Write" in kinds["verify"] and "Edit" in kinds["verify"], kinds
    assert "Write" in kinds["plan"], kinds
    settings = json.loads((out / "settings.json").read_text())
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "--deny" in command, command

def test_a_judging_stage_that_edits_the_tree_is_caught_however_it_did_it() -> None:
    """Denying Write does not stop `printf > file`, and a verify stage needs Bash.

    Measured on a live probe: with Write, Edit, MultiEdit and NotebookEdit all denied by flag
    and by hook, the model never attempted any of them and used a shell redirection instead.
    """
    import subprocess
    tmp = tempfile.mkdtemp()
    root = Path(tmp)
    (root / "src").mkdir()
    (root / "src/m.py").write_text(SAMPLE)
    out = root / "out"
    out.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
                 ["add", "-A"], ["commit", "-qm", "before"]):
        subprocess.run(["git"] + args, cwd=tmp, capture_output=True)

    class Meddler(Fake):
        def __call__(self, *a, **kw):
            (root / "src/m.py").write_text(SAMPLE + "\n# judged and edited\n")
            return super().__call__(*a, **kw)

    fake = Meddler([SOLID])
    original, dp.invoke = dp.invoke, fake
    dp.LOCK = root / "lock"
    try:
        head = dp.head_file(out)
        stage = {s.name: s for s in dp.DEFAULT_STAGES}["claims"]
        result = dp.run_stage(stage, cc_ledger.contract_for("review"), "t", "m", head, out,
                              root, False, False)
    finally:
        dp.invoke = original
    assert any("changed the working tree" in g for g in result.gaps), result.gaps

def test_a_lock_whose_holder_is_gone_is_not_a_lock() -> None:
    """A run killed mid-stage leaves the lock behind, and the next one asks a human whether a
    pid from an hour ago still means anything. The pid answers that.
    """
    assert dp.holder_alive("2026-08-02 16:40:11 pid %d model m" % os.getpid())
    assert not dp.holder_alive("2026-08-02 16:40:11 pid 999999 model m")
    assert dp.holder_alive("something with no pid in it")
