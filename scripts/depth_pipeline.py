#!/usr/bin/env python3
"""Run a task as a sequence of gated stages, one at a time, against the local 31B.

Two ways of running this pipeline exist and they must not drift apart: interactively, where Claude
Code's hooks inject the contract and the Stop gate refuses a thin answer, and unattended, which is
this file. Both call the *same* verifier and the same ledger, so a bench result and a session result
mean the same thing. That is the whole reason the driver exists rather than a shell loop.

Why stages, when parallel specialists would be nicer. One 31B runner fits in 128 GB and naming a
second variant evicts the first, so stages run strictly sequentially against one variant -- the
driver refuses to start if another run holds the lock. What stages buy is not concurrency but small
contexts: each stage reads the previous stage's artifact off disk instead of inheriting its
conversation, and prefill is the dominant cost here (292 s for a cold 106k prompt against 11.9
tok/s of decode).

Why the prompt head is hashed. Prefix-cache reuse is a match from token zero. Every stage sends a
byte-identical head, so the second and third stages restore it instead of prefilling it, and the
trie node they share is multi-child and therefore exempt from eviction (LOCAL_AGENT_OPS.md, §8).
The hash is asserted rather than assumed because a head that silently changes turns a 0.4 s restore
into a 13.6 s prefill and nothing in the output would say so.

Each stage gets exactly one refusal, matching the interactive gate: the spike measured a refused
round at 1.8x to 2.5x the cost of the original, so a second refusal would double a stage to buy
what the first already bought.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cc_evidence  # noqa: E402
import cc_ledger  # noqa: E402
import cc_verify  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
LOCK = Path("/tmp/depth-pipeline.lock")
OLLAMA_LOG = Path.home() / ".ollama" / "logs" / "server.log"
# Same regex as the ollama-watch skill's state.py, which is where the field names came from.
CACHE_HIT = re.compile(r"cache hit.*?total=(\d+)\s+matched=(\d+)")

# Tools a stage may use. Deliberately read-only plus Bash: a stage that can edit is a stage whose
# evidence describes a file it changed halfway through reading. Exactly one stage of an implement
# run is allowed the editing tools, so every other stage's citations refer to a tree it did not move.
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
        stance=("Now make the claims the survey supports, and only those. Open every file you "
                "cite, in this stage, before quoting it -- the survey is a map, not a substitute "
                "for reading, and it saw at most the first part of a long file. To cite a line "
                "beyond that, find it with a search and read around the hit; a line number you "
                "have not seen is a guess even when the claim is right. Each claim gets its own "
                "block. If the survey pointed somewhere you could not resolve, that is an "
                "UNKNOWN, not a guess."),
    ),
    Stage(
        name="adversary",
        produces="verdict.md",
        consumes=("claims.md",),
        stance=("Try to break each claim above. For each one, run the cheapest thing that would "
                "show it false and report what it printed; a claim you cannot attack survives, a "
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


@dataclass
class StageResult:
    stage: str
    session: str
    seconds: float = 0.0
    rounds: int = 1
    claims: int = 0
    gaps: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    cache: list[tuple[int, int]] = field(default_factory=list)
    answer: str = ""
    error: str = ""

    @property
    def reuse(self) -> float:
        total = sum(t for t, _ in self.cache)
        return 100.0 * sum(m for _, m in self.cache) / total if total else 0.0


def transcript_for(session: str, cwd: str) -> Path:
    munged = str(Path(cwd).resolve()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / munged / ("%s.jsonl" % session)


def log_offset() -> int:
    try:
        return OLLAMA_LOG.stat().st_size
    except OSError:
        return 0


def cache_since(offset: int) -> list[tuple[int, int]]:
    """Prefix-cache reuse recorded since `offset`. Empty when the log is unreadable, which is a
    missing measurement rather than a failure: Ollama logs a request only on completion."""
    try:
        with open(OLLAMA_LOG, errors="replace") as fh:
            fh.seek(offset)
            return [(int(m.group(1)), int(m.group(2))) for m in
                    (CACHE_HIT.search(line) for line in fh) if m]
    except OSError:
        return []


def compose(head: Path, contract: cc_ledger.Contract, stage: Stage, task: str,
            out_dir: Path, scratch: Path) -> str:
    parts = [cc_ledger.contract_markdown(contract), "", "STAGE: %s" % stage.name, stage.stance,
             "", "TASK: %s" % task]
    for name in stage.consumes:
        prior = out_dir / name
        if prior.is_file():
            parts += ["", "--- %s (from the previous stage) ---" % name,
                      prior.read_text(errors="replace").strip()]
    parts += ["",
              # Twelve repro scripts turned up in the repository root after six runs. A stage is
              # meant to be read-only, and Bash is the hole in that: it is the tool the adversary
              # stage exists for, so it cannot be taken away, only aimed somewhere harmless. The one
              # stage that may edit needs the opposite instruction, or it writes its fix to scratch.
              ("Change the files the task requires and nothing else. Scratch work -- a probe, a "
               "throwaway script -- still goes in %s, and no scratch file is part of your answer."
               % scratch) if stage.writes else
              ("Any scratch file you write -- a repro script, a probe, a scratch test -- goes in "
               "%s and nowhere else. Do not create files in the repository you are reviewing."
               % scratch),
              "", "Write your answer to the reply. It is checked, not read charitably."]
    return "\n".join(parts)


def settings_file(model: str, out_dir: Path) -> Path:
    """A settings file for this run, because the user's global one can veto the model.

    ~/.claude/settings.json may carry `enforceAvailableModels` with a list this model is not on --
    trivially so, since that list names models by hand and this one is built locally. The client
    then does not complain: it falls back to its cloud default, asks Ollama for claude-opus-4-8,
    gets a 404, and exits 1 with nothing on stderr, which reads exactly like a crashed stage.
    A session that supplies its own model cannot be overruled by a stale global list.
    """
    guard = "%s --stop-advice answer" % (REPO / "scripts/cc-context-guard.py")
    path = out_dir / "settings.json"
    path.write_text(json.dumps({
        "model": model,
        "availableModels": [model],
        "enforceAvailableModels": False,
        # Nothing compacts here. An unattended stage let loose on an unfamiliar repository reads
        # until it stops: one survey of a mid-sized web app reached 137k tokens against a 98k
        # window, at which point the runner is growing KV into swap and every turn costs minutes.
        # The same guard the interactive launcher uses caps a single read and stops the gathering
        # at 80% of the window, which turns that run into an answer with UNKNOWNs instead.
        "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": guard}]}]},
    }, indent=2) + "\n")
    return path


def invoke(prompt: str, model: str, head: Path, session: str, cwd: Path, settings: Path,
           resume: bool = False, yolo: bool = False, timeout: int = 3600,
           tools: str = STAGE_TOOLS) -> tuple[str, str]:
    """One `claude -p` turn. Returns (text, error)."""
    cmd = ["claude", "-p", prompt, "--model", model,
           "--append-system-prompt-file", str(head),
           "--settings", str(settings),
           "--allowed-tools", tools,
           "--output-format", "json"]
    cmd += ["--resume", session] if resume else ["--session-id", session]
    if yolo:
        cmd.append("--dangerously-skip-permissions")
    env = dict(os.environ)
    env.update({
        # Ollama is the default backend, not the only one. A caller that has already pointed
        # ANTHROPIC_BASE_URL somewhere -- llama-server behind the proxy, say -- means it, and
        # overwriting it here sent every request to Ollama, which answered that the model does not
        # exist. The error names the model, so it reads as a bad --model rather than as this line.
        "ANTHROPIC_BASE_URL": (env.get("ANTHROPIC_BASE_URL")
                               or env.get("OLLAMA_HOST_URL", "http://127.0.0.1:11434")),
        "ANTHROPIC_AUTH_TOKEN": "ollama",
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_CODE_ENABLE_AWAY_SUMMARY": "0",
        "API_TIMEOUT_MS": env.get("CLAUDE_GEMMA_TIMEOUT_MS", "1800000"),
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": env.get("CLAUDE_GEMMA_MAX_OUTPUT", "8192"),
        # The gate is the driver's job here; leaving the hooks armed would refuse the same answer
        # twice with two different mechanisms.
        "CC_DEPTH_ADAPTER": "",
    })
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              cwd=str(cwd), env=env)
    except subprocess.TimeoutExpired:
        return "", "timed out after %ds" % timeout
    if proc.returncode != 0:
        # The client reports an API error as JSON on stdout and leaves stderr empty, so a bare
        # "exited 1" hides the only sentence that says what went wrong.
        said = proc.stderr.strip() or proc.stdout.strip()
        try:
            said = str(json.loads(said).get("result") or said)
        except ValueError:
            pass
        return "", "claude exited %d: %s" % (proc.returncode, said[:300])
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        return proc.stdout, ""
    return str(payload.get("result") or ""), str(payload.get("error") or "")


def load_gate():
    """Import the hook by path, because its filename is a hyphenated executable, not a module name.

    Registered in sys.modules before execution: dataclasses resolve annotations through
    `sys.modules[cls.__module__]`, and an unregistered module makes that lookup return None.
    """
    if "depth_gate" in sys.modules:
        return sys.modules["depth_gate"]
    import importlib.util
    spec = importlib.util.spec_from_file_location("depth_gate", REPO / "scripts/cc-depth-gate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["depth_gate"] = module
    spec.loader.exec_module(module)
    return module


def check(answer: str, contract: cc_ledger.Contract, session: str, cwd: Path,
          predicted: tuple = ()):
    """The interactive gate's arithmetic, reused verbatim so both paths agree."""
    gate = load_gate()
    claims, unknowns = cc_ledger.claims_from_text(answer)
    transcript = transcript_for(session, str(cwd))
    calls = cc_evidence.collect(str(transcript)) if transcript.is_file() else []
    if not transcript.is_file():
        print("   note: no transcript at %s -- citations checked against the files, but not "
              "against what this stage read" % transcript, file=sys.stderr)
    gaps, report = gate.evaluate(contract, claims, unknowns, calls, str(cwd),
                                 check_coverage=transcript.is_file(), answer=answer,
                                 predicted=predicted)
    report["coverage_checked"] = transcript.is_file()
    return claims, unknowns, gaps, report, gate


def run_stage(stage: Stage, contract: cc_ledger.Contract, task: str, model: str, head: Path,
              out_dir: Path, cwd: Path, yolo: bool, dry_run: bool) -> StageResult:
    session = str(uuid.uuid4())
    scratch = out_dir / "scratch"
    scratch.mkdir(exist_ok=True)
    contract = cc_ledger.contract_for(stage.adapter) if stage.adapter else contract
    # What the plan committed to, read off disk rather than carried in memory, so a stage rerun on
    # its own reaches the same verdict as one inside a full run.
    predicted = ()
    if contract.needs_red_green:
        plan = out_dir / "plan.md"
        if plan.is_file():
            predicted = tuple(cc_verify.predictions(plan.read_text(errors="replace")))
    prompt = compose(head, contract, stage, task, out_dir, scratch)
    (out_dir / ("%s.prompt.txt" % stage.name)).write_text(prompt)
    result = StageResult(stage=stage.name, session=session)
    if dry_run:
        result.answer = "(dry run)"
        return result

    started = time.time()
    offset = log_offset()
    settings = settings_file(model, out_dir)
    answer, error = invoke(prompt, model, head, session, cwd, settings, yolo=yolo,
                           tools=stage.tools)
    if error and not answer:
        result.error = error
        result.seconds = time.time() - started
        result.cache = cache_since(offset)
        return result

    if stage.verify:
        claims, unknowns, gaps, report, gate = check(answer, contract, session, cwd, predicted)
        if gaps:
            # One refusal, same wording the Stop hook uses, so the two paths train the same habit.
            refusal = gate.refusal(gaps, out_dir / "claims.jsonl")
            second, error = invoke(refusal, model, head, session, cwd, settings,
                                   resume=True, yolo=yolo, tools=stage.tools)
            result.rounds = 2
            if second:
                answer = second
                claims, unknowns, gaps, report, _ = check(answer, contract, session, cwd,
                                                          predicted)
        result.claims, result.unknowns, result.gaps = len(claims), unknowns, gaps
        report.update({"gaps": gaps, "rounds": result.rounds, "stage": stage.name})
        (out_dir / ("%s.gate.json" % stage.name)).write_text(json.dumps(report, indent=2) + "\n")

    result.answer = answer
    result.seconds = time.time() - started
    result.cache = cache_since(offset)
    (out_dir / stage.produces).write_text(answer)
    return result


def head_file(out_dir: Path) -> Path:
    """The byte-identical head every stage sends. Composed once per run, then hashed."""
    parts = []
    for name in ("prompts/skeptic_min.md",):
        p = REPO / name
        if p.is_file():
            parts.append(p.read_text())
    parts.append("Write in plain text that a terminal can display, including symbols in prose: no "
                 "LaTeX, MathJax or dollar-delimited markup anywhere.\n")
    head = out_dir / "head.md"
    head.write_text("\n".join(parts))
    return head


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("task", help="the question the pipeline answers")
    ap.add_argument("--adapter", default="review", choices=sorted(cc_ledger.ADAPTERS))
    ap.add_argument("--model", default=os.environ.get("DEPTH_MODEL", "gemma4-31b-mtp-96k"))
    ap.add_argument("--stages", default="survey,claims,adversary",
                    help="review flow: survey,claims,adversary; change flow: plan,implement,verify")
    ap.add_argument("--out", default="")
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--yolo", action="store_true",
                    help="let stages run commands without a permission prompt")
    ap.add_argument("--dry-run", action="store_true",
                    help="compose the prompts and assert the head, call no model")
    args = ap.parse_args()

    by_name = {s.name: s for s in DEFAULT_STAGES + IMPLEMENT_STAGES}
    try:
        stages = [by_name[n.strip()] for n in args.stages.split(",") if n.strip()]
    except KeyError as exc:
        print("unknown stage %s; known: %s" % (exc, ", ".join(by_name)), file=sys.stderr)
        return 2

    contract_check = cc_ledger.contract_for(args.adapter)
    if contract_check.needs_red_green and not any(s.writes for s in stages):
        # Cheap, because the alternative is finding out forty minutes later that every stage was
        # read-only and the run could not have satisfied its own contract.
        print("adapter %s must show a command failing and then passing, and no selected stage may "
              "write. Add the implement stage." % args.adapter, file=sys.stderr)
        return 2

    # One runner, one variant, one stage at a time. A second driver would evict the first's model.
    if LOCK.exists() and not args.dry_run:
        print("another pipeline holds %s (started %s). Wait, or remove it if it is stale."
              % (LOCK, LOCK.read_text().strip()[:60]), file=sys.stderr)
        return 1

    out_dir = Path(args.out or (Path(args.cwd) / "artifacts/pipeline"
                                / time.strftime("%Y%m%d-%H%M%S")))
    out_dir.mkdir(parents=True, exist_ok=True)
    head = head_file(out_dir)
    head_hash = hashlib.sha256(head.read_bytes()).hexdigest()[:12]
    contract = cc_ledger.contract_for(args.adapter)

    if not args.dry_run:
        LOCK.write_text("%s pid %d model %s\n" % (time.strftime("%F %T"), os.getpid(), args.model))
    results: list[StageResult] = []
    try:
        for stage in stages:
            print("== %s (%s, %s) ..." % (stage.name, args.adapter, args.model), flush=True)
            r = run_stage(stage, contract, args.task, args.model, head, out_dir,
                          Path(args.cwd), args.yolo, args.dry_run)
            results.append(r)
            if hashlib.sha256(head.read_bytes()).hexdigest()[:12] != head_hash:
                print("   head changed mid-run: every later stage now re-prefills it",
                      file=sys.stderr)
            if r.error:
                print("   failed: %s" % r.error, file=sys.stderr)
                break
            print("   %.0fs, %d round(s), %d claim(s), %d gap(s), %d unknown(s), reuse %.1f%%"
                  % (r.seconds, r.rounds, r.claims, len(r.gaps), len(r.unknowns), r.reuse),
                  flush=True)
    finally:
        if not args.dry_run:
            LOCK.unlink(missing_ok=True)

    summary = {
        "task": args.task, "adapter": args.adapter, "model": args.model,
        "head_sha256_12": head_hash, "out": str(out_dir),
        "stages": [{"stage": r.stage, "session": r.session, "seconds": round(r.seconds, 1),
                    "rounds": r.rounds, "claims": r.claims, "gaps": r.gaps,
                    "unknowns": r.unknowns, "reuse_pct": round(r.reuse, 1), "error": r.error}
                   for r in results],
    }
    (out_dir / "run.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("\nartifacts in %s (head %s)" % (out_dir, head_hash))
    return 1 if any(r.error for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
