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

    def __call__(self, prompt, model, head, session, cwd, settings, resume=False, yolo=False,
                 timeout=3600, tools=dp.STAGE_TOOLS):
        self.prompts.append(prompt)
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
        results = []
        for name in stages.split(","):
            stage = {s.name: s for s in dp.DEFAULT_STAGES + dp.IMPLEMENT_STAGES}[name]
            results.append(dp.run_stage(stage, contract, task, "fake-model", head, out, root,
                                        False, False))
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


def test_only_the_implement_stage_is_handed_the_editing_tools() -> None:
    """The plan reads and the verify stage judges; a stage that could edit either would be judging
    a tree it had moved."""
    _, fake, _, _ = _run(["where the bug is", "changed it", "stashed it and it failed"],
                         stages="plan,implement,verify", adapter="implement")
    handed = dict(zip(["plan", "implement", "verify"], fake.tools))
    assert "Edit" not in handed["plan"] and "Write" not in handed["plan"], handed
    assert "Edit" in handed["implement"] and "Write" in handed["implement"], handed
    assert "Edit" not in handed["verify"] and "Write" not in handed["verify"], handed


def test_the_writing_stage_is_told_to_change_the_repository_not_scratch() -> None:
    """The read-only stages are told the opposite, and that instruction would send a fix to /tmp."""
    _, fake, out, _ = _run(["where the bug is", "changed it", "stashed it and it failed"],
                           stages="plan,implement,verify", adapter="implement")
    plan, implement = fake.prompts[0], fake.prompts[1]
    assert "Do not create files in the repository" in plan, plan[-300:]
    assert "Change the files the task requires" in implement, implement[-300:]
    assert str(out / "scratch") in implement, implement[-300:]
