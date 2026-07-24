#!/usr/bin/env python3.14
"""Claim probe: true/false statements about shopapi — breaks soft ties.

Each claim is objectively true or false in the fixture. Models must tool-explore
then return a boolean vector. Scoring is Hamming accuracy; wrong confidence
hurts (no "skip"). Highly discriminative vs vague architecture essays.

Usage:
  python run.py run claim
  BENCH_SELFTEST=1 python -m benches.claim
  BENCH_MODEL='qwen3-coder-next:q8_0' python -m benches.claim
  BENCH_PROVIDER=cursor BENCH_MODEL='composer-2.5' python -m benches.claim
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bench_lib.assignment import load_simple_claims_yaml  # noqa: E402
from bench_lib.ollama_chat import chat as ollama_chat  # noqa: E402
from bench_lib.ollama_think import (  # noqa: E402
    RoundTranscript,
    default_num_predict,
    format_think_combined,
    parse_think,
    save_task_transcript,
    think_for_round,
    think_loop_nudge,
)
from bench_lib.paths import results_dir  # noqa: E402
from benches.shopapi.tools import FIXTURE_ROOT, ToolSession  # noqa: E402

OUT_DIR = results_dir("archbench")
_CLAIMS_PATH = Path(__file__).resolve().parent / "claims.yaml"

SELFTEST = os.environ.get("BENCH_SELFTEST") == "1"
PROVIDER = os.environ.get("BENCH_PROVIDER", "ollama").strip().lower()
MODEL = "selftest" if SELFTEST else os.environ.get("BENCH_MODEL", "")
_TAG_BASE = re.sub(r"[^a-zA-Z0-9._-]", "_", MODEL or "model")
TAG = os.environ.get(
    "BENCH_TAG",
    "selftest_claim"
    if SELFTEST
    else f"{'cursor_' if PROVIDER in ('cursor', 'cursor-cli', 'agent') else ''}{_TAG_BASE}_claim",
)
FIXTURE = FIXTURE_ROOT

THINK = parse_think()
OPTIONS = {
    "temperature": float(os.environ.get("BENCH_TEMPERATURE", "0.1")),
    "num_ctx": int(os.environ.get("BENCH_NUM_CTX", "65536")),
    "num_predict": default_num_predict(8192, think_base=24576),
}
MAX_ROUNDS = int(os.environ.get("BENCH_MAX_ROUNDS", "40"))
MAX_TOOL_CALLS = int(os.environ.get("BENCH_MAX_TOOL_CALLS", "40"))
HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")

# Assignment: benches/claim/claims.yaml  →  (id, text, gold)
_CLAIMS_RAW = load_simple_claims_yaml(_CLAIMS_PATH)
CLAIMS: list[tuple[str, str, bool]] = [
    (str(c["id"]), str(c["text"]), bool(c["gold"])) for c in _CLAIMS_RAW
]
if len(CLAIMS) < 20:
    raise SystemExit(f"claims.yaml looks empty/broken: {len(CLAIMS)} claims from {_CLAIMS_PATH}")


def chat(
    model: str,
    messages: list[dict[str, str]],
    *,
    think: bool | str | None = None,
    on_thinking=None,
    on_content=None,
) -> dict[str, Any]:
    return ollama_chat(
        model,
        messages,
        options=OPTIONS,
        think=THINK if think is None else think,
        on_thinking=on_thinking,
        on_content=on_content,
    )


_TOOL_RE = re.compile(
    r"<(?:arch_tool|tool_call)>\s*(\{[\s\S]*?\})\s*</(?:arch_tool|tool_call)>",
    re.I,
)
_FINAL_RE = re.compile(
    r"<(?:arch_final|final_answer)>\s*([\s\S]*?)\s*</(?:arch_final|final_answer)>",
    re.I,
)


def parse_tool_call(text: str) -> dict[str, Any] | None:
    m = _TOOL_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


_FENCE_JSON = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.I)


def parse_final(text: str) -> dict[str, Any] | None:
    """Extract claim JSON from arch_final, a ```json fence, or a bare object.

    Prefer fenced JSON over a greedy ``{..."answers"...}`` scan: Cursor ask-mode
    answers often put a valid fence first, then more prose/code with ``}`` that
    poisons the greedy match (composer/haiku scored 0/23 with a perfect fence).
    """
    text = text or ""
    candidates: list[str] = []
    m = _FINAL_RE.search(text)
    if m:
        candidates.append(m.group(1).strip())
    # All json fences — try last first (final answer usually lands at the end).
    fences = _FENCE_JSON.findall(text)
    candidates.extend(reversed(fences))
    m2 = re.search(r"\{[^}]*\"answers\"[^}]*\}", text)
    if m2:
        candidates.append(m2.group(0))

    for blob in candidates:
        if not blob:
            continue
        fence = _FENCE_JSON.search(blob)
        if fence:
            blob = fence.group(1).strip()
        else:
            blob = re.sub(r"^```(?:json)?\s*", "", blob.strip(), flags=re.I)
            blob = re.sub(r"\s*```$", "", blob.strip())
        for attempt in (blob, re.sub(r",\s*}", "}", re.sub(r",\s*]", "]", blob))):
            try:
                obj = json.loads(attempt)
                if isinstance(obj, dict) and "answers" in obj:
                    return obj
            except json.JSONDecodeError:
                continue
    return None


def coerce_bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes", "t", "1"):
            return True
        if s in ("false", "no", "f", "0"):
            return False
    return None


def grade_answers(answer: dict[str, Any] | None, session: ToolSession) -> dict[str, Any]:
    answers = (answer or {}).get("answers") or {}
    if isinstance(answers, list):
        # [{id, value}] or [bool] in claim order
        tmp: dict[str, Any] = {}
        if answers and isinstance(answers[0], dict):
            for item in answers:
                tmp[str(item.get("id"))] = item.get("value", item.get("true"))
        else:
            for (cid, _, _), val in zip(CLAIMS, answers):
                tmp[cid] = val
        answers = tmp

    correct = 0
    wrong = 0
    missing = 0
    per: list[dict[str, Any]] = []
    for cid, text, gold in CLAIMS:
        got = coerce_bool(answers.get(cid))
        if got is None:
            missing += 1
            per.append({"id": cid, "gold": gold, "got": None, "ok": False})
            continue
        ok = got is gold
        if ok:
            correct += 1
        else:
            wrong += 1
        per.append({"id": cid, "gold": gold, "got": got, "ok": ok, "claim": text})

    # Evidence: must have read at least 4 distinct code files
    code_reads = [f for f in session.files_read if f.endswith(".py")]
    ev = min(3, len(set(code_reads)) // 2)  # 0..3
    # Score: 1 pt per correct claim + evidence bonus; wrong/missing = 0 for that claim
    score = correct + ev
    max_score = len(CLAIMS) + 3
    return {
        "score": score,
        "max_score": max_score,
        "correct": correct,
        "wrong": wrong,
        "missing": missing,
        "evidence_bonus": ev,
        "files_read": sorted(session.files_read),
        "per_claim": per,
        "ok": correct >= int(0.8 * len(CLAIMS)),
        "detail": f"{correct}/{len(CLAIMS)} correct, wrong={wrong}, missing={missing}, ev=+{ev}",
    }


_CLAIMS_BLOCK = "\n".join(f'- {cid}: "{text}"' for cid, text, _ in CLAIMS)

PROMPT = f"""You are investigating the shopapi Python repo (tools only — do not invent files).

Tools — one at a time (use arch_tool, NOT tool_call):
<arch_tool>
{{"name": "TOOL_NAME", "arguments": {{..}}}}
</arch_tool>

Tools: list_dir, read_file, grep, find_refs. Budget: 40 calls.

Judge EACH claim true or false based on code evidence. Guessing is punished.

Claims:
{_CLAIMS_BLOCK}

Finish with:
<arch_final>
{{
  "answers": {{
    "c01": true,
    "c02": false
  }},
  "citations": {{"c01": ["path.py:symbol"]}}
}}
</arch_final>

Every claim id listed above must appear with a boolean.
"""

CURSOR_PROMPT = f"""You are investigating the shopapi Python repo (workspace root = shopapi).
Use your built-in tools to inspect the code. Do not invent files.

Judge EACH claim true or false based on code evidence. Guessing is punished.

Claims:
{_CLAIMS_BLOCK}

Finish with a JSON object (optionally in a ```json fence or <arch_final> wrapper):
{{
  "answers": {{
    "c01": true,
    "c02": false
  }},
  "citations": {{"c01": ["path.py:symbol"]}}
}}

Every claim id listed above must appear with a boolean.
"""


def run_selftest() -> int:
    session = ToolSession(max_calls=40)
    for p in [
        "api/orders.py",
        "service/order_service.py",
        "service/payment_service.py",
        "service/invoice_service.py",
        "store/invoice_repo.py",
        "worker/outbox_worker.py",
        "worker/webhook_retry.py",
        "README.md",
    ]:
        session.dispatch("read_file", {"path": p})
    gold = {cid: val for cid, _, val in CLAIMS}
    g = grade_answers({"answers": gold}, session)
    print(json.dumps(g, indent=2))
    if g["correct"] != len(CLAIMS) or g["score"] < len(CLAIMS):
        print("SELFTEST FAILED", file=sys.stderr)
        return 1
    # decoy exists but must NOT be on DELETE path (claim false)
    hits = session.dispatch("grep", {"pattern": "cancel_order_legacy"})
    assert hits.get("ok") and hits.get("hits"), "decoy cancel_order_legacy missing"
    api = session.dispatch("read_file", {"path": "api/orders.py"})
    assert "cancel_order_legacy" not in (api.get("content") or "")
    print("SELFTEST OK")
    return 0


def run_model_ollama() -> dict[str, Any]:
    session = ToolSession(max_calls=MAX_TOOL_CALLS)
    messages = [{"role": "user", "content": PROMPT}]
    transcript = RoundTranscript(OUT_DIR, TAG, "claim")
    totals = {"wall_s": 0.0, "prompt_tokens": 0, "eval_tokens": 0, "rounds": 0, "done_reason": None}
    final = None
    think_loop_nudges = 0
    for round_i in range(MAX_ROUNDS):
        totals["rounds"] = round_i + 1
        transcript.begin_round(round_i + 1)
        round_think = think_for_round(round_i, THINK)
        resp = chat(
            MODEL,
            messages,
            think=round_think,
            on_thinking=transcript.on_thinking_delta,
            on_content=transcript.on_content_delta,
        )
        totals["wall_s"] += resp["wall_s"]
        totals["prompt_tokens"] += resp["prompt_tokens"]
        totals["eval_tokens"] += resp["eval_tokens"]
        totals["done_reason"] = resp["done_reason"]
        content = resp["content"] or ""
        thinking = resp.get("thinking") or ""
        transcript.end_round(
            thinking=thinking,
            content=content,
            done_reason=resp.get("done_reason"),
            eval_tokens=resp.get("eval_tokens"),
        )
        abort_reason = resp.get("done_reason") or ""
        if abort_reason in ("think_loop", "think_budget"):
            transcript.add_note(
                f"{abort_reason.upper()} aborted: "
                f"{resp.get('think_loop_detail') or abort_reason}"
            )
        if resp.get("think_promoted"):
            transcript.add_note("THINK_PROMOTED: final scraped from thinking")
        messages.append({"role": "assistant", "content": content})
        final = parse_final(content)
        if final is not None and parse_tool_call(content) is None:
            break
        if abort_reason in ("think_loop", "think_budget") and think_loop_nudges < 2:
            think_loop_nudges += 1
            nudge = think_loop_nudge(thinking=thinking, protocol="claim")
            transcript.add_note(f"{abort_reason} nudge {think_loop_nudges}/2")
            messages.append({"role": "user", "content": nudge})
            continue
        call = parse_tool_call(content)
        if call is None:
            nudge = "Emit one <arch_tool> or <arch_final> with all c01..c20 booleans."
            transcript.add_note(nudge)
            messages.append({"role": "user", "content": nudge})
            continue
        name = str(call.get("name") or "")
        args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        result = session.dispatch(name, args)
        transcript.add_tool(name, args, result.get("ok"))
        messages.append(
            {
                "role": "user",
                "content": "<arch_result>\n" + json.dumps(result)[:12000] + "\n</arch_result>",
            }
        )
    grade = grade_answers(final, session)
    transcript_path = transcript.save()
    return {
        "model": MODEL,
        "provider": "ollama",
        "bench": "claim",
        "tag": TAG,
        **grade,
        "answer": final,
        "tool_calls": len(session.calls),
        "wall_s": round(totals["wall_s"], 2),
        "prompt_tokens": totals["prompt_tokens"],
        "eval_tokens": totals["eval_tokens"],
        "rounds": totals["rounds"],
        "done_reason": totals["done_reason"],
        "num_ctx": OPTIONS["num_ctx"],
        "num_predict": OPTIONS["num_predict"],
        "thinking_chars": transcript.thinking_chars,
        "transcript": str(transcript_path),
    }


def run_model_cursor() -> dict[str, Any]:
    from bench_lib import cursor_cli

    resp = cursor_cli.chat(
        MODEL,
        CURSOR_PROMPT,
        mode=os.environ.get("BENCH_CURSOR_MODE", "ask"),
        workspace=FIXTURE,
    )
    content = resp.get("content") or ""
    thinking = resp.get("thinking") or ""
    final = parse_final(content)
    # Evidence requires real tool reads; Cursor ask-mode has no tool trace → ev=0.
    session = ToolSession(max_calls=MAX_TOOL_CALLS)
    grade = grade_answers(final, session)
    transcript_path = save_task_transcript(
        OUT_DIR, TAG, "claim", format_think_combined(content, thinking)
    )
    return {
        "model": MODEL,
        "provider": "cursor",
        "bench": "claim",
        "tag": TAG,
        **grade,
        "answer": final,
        "tool_calls": None,
        "wall_s": round(float(resp.get("wall_s") or 0), 2),
        "prompt_tokens": int(resp.get("prompt_tokens") or 0),
        "eval_tokens": int(resp.get("eval_tokens") or 0),
        "rounds": 1,
        "done_reason": resp.get("done_reason"),
        "num_ctx": OPTIONS["num_ctx"],
        "num_predict": OPTIONS["num_predict"],
        "thinking_chars": len(thinking),
        "transcript": str(transcript_path),
        "raw_content": content,
        "session_id": resp.get("session_id"),
    }


def run_model() -> dict[str, Any]:
    if PROVIDER in ("cursor", "cursor-cli", "agent"):
        return run_model_cursor()
    if PROVIDER != "ollama":
        raise SystemExit(f"Unknown BENCH_PROVIDER={PROVIDER!r} (use ollama|cursor)")
    return run_model_ollama()


def main() -> int:
    if SELFTEST:
        return run_selftest()
    if not MODEL:
        raise SystemExit("Set BENCH_MODEL or BENCH_SELFTEST=1")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    try:
        if PROVIDER in ("cursor", "cursor-cli", "agent"):
            from bench_lib import cursor_cli

            cursor_cli.chat(
                MODEL,
                "Reply with the single word: pong",
                mode="ask",
                workspace=FIXTURE,
            )
        else:
            chat(MODEL, [{"role": "user", "content": "pong"}])
    except Exception as e:  # noqa: BLE001
        print(f"warmup failed: {e}", file=sys.stderr)
        return 2
    result = run_model()
    path = OUT_DIR / f"{TAG}_{stamp}.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    (OUT_DIR / f"{TAG}_latest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in result if k not in ("per_claim", "answer", "raw_content")}, indent=2))
    print("WROTE", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
