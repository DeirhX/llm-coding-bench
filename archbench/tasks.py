"""Archbench tasks: prompts, gold answers, graders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from tools import ToolSession


def _norm_sym(s: str) -> str:
    s = str(s).strip()
    s = s.replace("\\", "/")
    # allow Module.func or path:func — keep last two dotted pieces or whole
    return s


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [_norm_sym(v)]
    if isinstance(v, list):
        return [_norm_sym(x) for x in v]
    return []


def _set_score(got: list[str], gold: list[str], *, unordered: bool = True) -> tuple[int, int, str]:
    """Return (score, max, detail). max = len(gold). Partial credit for subset."""
    gset = {_norm_sym(x) for x in gold}
    got_n = [_norm_sym(x) for x in got]
    if not unordered:
        # prefix match scoring
        score = 0
        for i, g in enumerate(gold):
            if i < len(got_n) and got_n[i] == _norm_sym(g):
                score += 1
            else:
                break
        detail = f"ordered {score}/{len(gold)}; got={got_n}"
        return score, len(gold), detail
    hit = sum(1 for x in gset if x in set(got_n))
    extra = [x for x in got_n if x not in gset]
    detail = f"hit {hit}/{len(gset)}"
    if extra:
        detail += f"; extras={extra[:8]}"
    return hit, len(gset), detail


def _citation_bonus(
    answer: dict[str, Any],
    session: ToolSession,
    required_files: list[str],
) -> tuple[int, int, str]:
    """Up to len(required_files) points: cited or actually read."""
    cites = _as_list(answer.get("citations"))
    cite_files = set()
    for c in cites:
        # path:symbol or path
        path = c.split(":")[0]
        path = path.replace("fixture/shopapi/", "").replace("shopapi/", "")
        cite_files.add(path)
    read = set(session.files_read)
    score = 0
    missing = []
    for f in required_files:
        if f in cite_files or f in read:
            score += 1
        else:
            missing.append(f)
    detail = f"evidence {score}/{len(required_files)}"
    if missing:
        detail += f"; missing={missing}"
    return score, len(required_files), detail


def _parse_answer(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    return {}


@dataclass
class Task:
    id: str
    title: str
    family: str
    max_score: int
    prompt: str
    required_files: list[str]
    grade: Callable[[dict[str, Any], ToolSession], dict[str, Any]]


def grade_delete_chain(answer: dict[str, Any], session: ToolSession) -> dict[str, Any]:
    """Call chain for DELETE /orders/{id}."""
    a = _parse_answer(answer)
    gold_chain = [
        "api.orders.handle_delete_order",
        "pkg.auth.authenticate",
        "service.order_service.cancel_order",
        "store.order_repo.soft_delete",
        "store.outbox.insert",
        "store.cache.invalidate_order",
    ]
    # Accept short names for key steps
    aliases = {
        "handle_delete_order": "api.orders.handle_delete_order",
        "authenticate": "pkg.auth.authenticate",
        "cancel_order": "service.order_service.cancel_order",
        "soft_delete": "store.order_repo.soft_delete",
        "insert": "store.outbox.insert",
        "outbox.insert": "store.outbox.insert",
        "invalidate_order": "store.cache.invalidate_order",
        "cache.invalidate_order": "store.cache.invalidate_order",
    }
    chain_in = _as_list(a.get("chain"))
    chain_norm = [aliases.get(x, aliases.get(x.split(".")[-1], x)) for x in chain_in]
    # Score presence of critical hops (unordered set of required symbols)
    required = [
        "handle_delete_order",
        "cancel_order",
        "soft_delete",
        "insert",
        "invalidate_order",
    ]
    flat = " ".join(chain_norm).replace(" ", "")
    # also join with dots for search
    blob = " | ".join(chain_norm + chain_in).lower()
    hit = 0
    for r in required:
        if r.lower() in blob:
            hit += 1
    chain_score, chain_max = hit, len(required)

    gold_effects = {"outbox OrderCancelled", "soft-delete order", "invalidate order cache"}
    effects = {_norm_sym(x).lower() for x in _as_list(a.get("side_effects"))}
    eff_hit = 0
    checks = [
        ("outbox", "ordercancelled", "cancel"),
        ("soft", "delete", "deleted"),
        ("cache", "invalidate"),
    ]
    for group in checks:
        if any(any(tok in e for tok in group) for e in effects):
            eff_hit += 1
    eff_max = 3

    ev_s, _, ev_d = _citation_bonus(a, session, ["api/orders.py", "service/order_service.py"])
    # weights: chain 5, effects 3, evidence 2 = 10
    score = round(chain_score / chain_max * 5) + round(eff_hit / eff_max * 3) + min(2, ev_s)
    detail = f"chain {chain_score}/{chain_max}; effects {eff_hit}/{eff_max}; {ev_d}"
    return {"score": score, "max_score": 10, "detail": detail, "ok": score >= 8}


def grade_payment_chain(answer: dict[str, Any], session: ToolSession) -> dict[str, Any]:
    a = _parse_answer(answer)
    blob = " | ".join(_as_list(a.get("chain")) + _as_list(a.get("side_effects"))).lower()
    required = [
        ("handle_payment_webhook", "api/webhooks"),
        ("handle_payment_webhook", "payment_service"),
        ("outbox", "order_paid"),
        ("mark_paid",),
        ("_mark_processed", "processed_webhooks", "idempoten"),
    ]
    # simpler token groups
    groups = [
        ["handle_payment_webhook"],
        ["payment_service", "handle_payment_webhook"],
        ["order_paid", "outbox.insert", "outbox"],
        ["mark_paid"],
        ["_mark_processed", "processed_webhook", "idempoten"],
    ]
    # Actually grade specific hops
    hops = [
        ["webhooks.handle_payment_webhook", "handle_payment_webhook"],
        ["payment_service.handle_payment_webhook"],
        ["outbox.insert", "order_paid"],
        ["order_service.mark_paid", "mark_paid"],
        ["_mark_processed", "processed_webhooks"],
    ]
    chain_blob = " | ".join(_as_list(a.get("chain"))).lower()
    hit = 0
    for alts in hops:
        if any(a.lower() in chain_blob for a in alts):
            hit += 1
    # side effect: writes payment row
    effects = " | ".join(_as_list(a.get("side_effects"))).lower()
    eff = 1 if any(t in effects for t in ("payment", "charge", "db.put", "payments")) else 0
    ev_s, _, ev_d = _citation_bonus(
        a, session, ["api/webhooks.py", "service/payment_service.py"]
    )
    score = hit + eff + min(2, ev_s)  # max 5+1+2=8 → scale to 10
    # hit max 5, eff 1, ev 2 = 8; remap to 10
    raw_max = 8
    score10 = round(score / raw_max * 10)
    return {
        "score": score10,
        "max_score": 10,
        "detail": f"hops {hit}/5; payment_effect={eff}; {ev_d}",
        "ok": score10 >= 8,
    }


def grade_tenant_invoice(answer: dict[str, Any], session: ToolSession) -> dict[str, Any]:
    a = _parse_answer(answer)
    enforced = " | ".join(_as_list(a.get("enforced_at"))).lower()
    bypasses = " | ".join(_as_list(a.get("bypasses"))).lower()
    # enforcement: list_invoices / list_by_tenant
    enf_pts = 0
    if "list_invoices" in enforced or "list_by_tenant" in enforced:
        enf_pts += 2
    if "invoice_service.list" in enforced or "list_invoices" in enforced:
        enf_pts = max(enf_pts, 2)
    # bypasses: get_invoice / get_by_id without tenant check; admin_export
    by_pts = 0
    if "get_invoice" in bypasses or "get_by_id" in bypasses:
        by_pts += 3
    if "admin_export" in bypasses or "export_all" in bypasses:
        by_pts += 3
    ev_s, _, ev_d = _citation_bonus(
        a, session, ["service/invoice_service.py", "store/invoice_repo.py"]
    )
    score = min(4, enf_pts + 2) // 1  # messy
    score = 0
    score += 2 if ("list_invoices" in enforced or "list_by_tenant" in enforced) else 0
    score += 3 if ("get_invoice" in bypasses or "get_by_id" in bypasses) else 0
    score += 3 if ("admin_export" in bypasses or "export_all" in bypasses) else 0
    score += min(2, ev_s)
    return {
        "score": score,
        "max_score": 10,
        "detail": f"enforced/bypass scoring; {ev_d}; enf={enforced[:120]!r} by={bypasses[:120]!r}",
        "ok": score >= 8,
    }


def grade_review_n_plus_one(answer: dict[str, Any], session: ToolSession) -> dict[str, Any]:
    a = _parse_answer(answer)
    findings = a.get("findings") or []
    if isinstance(findings, dict):
        findings = [findings]
    blob = json_dumps_lower(findings + [a])
    pts = 0
    # must identify N+1 in list_orders
    if "n+1" in blob or "n_plus_1" in blob or ("list_orders" in blob and "get_order" in blob):
        pts += 4
    elif "list_orders" in blob and ("loop" in blob or "per order" in blob or "one by one" in blob):
        pts += 3
    # must NOT claim auth is broken on list_orders
    false_auth = ("auth" in blob and "broken" in blob) or ("authenticate" in blob and "missing" in blob)
    if not false_auth:
        pts += 2
    else:
        pts += 0
    # severity reasonable
    sev = str(a.get("max_severity") or a.get("severity") or "").lower()
    if sev in ("medium", "med", "high", "warning", "p2", "p3"):
        pts += 2
    ev_s, _, ev_d = _citation_bonus(a, session, ["service/order_service.py"])
    pts += min(2, ev_s)
    return {"score": min(10, pts), "max_score": 10, "detail": f"{ev_d}; false_auth={false_auth}", "ok": pts >= 8}


def grade_review_cache_i4(answer: dict[str, Any], session: ToolSession) -> dict[str, Any]:
    a = _parse_answer(answer)
    blob = json_dumps_lower([a])
    pts = 0
    if "mark_paid" in blob and ("cache" in blob or "invalidate" in blob):
        pts += 5
    elif "i4" in blob or ("status" in blob and "cache" in blob):
        pts += 3
    if "invalidate" in blob and ("missing" in blob or "not" in blob or "bug" in blob or "fail" in blob):
        pts += 3
    ev_s, _, ev_d = _citation_bonus(a, session, ["service/order_service.py"])
    pts += min(2, ev_s)
    return {"score": min(10, pts), "max_score": 10, "detail": ev_d, "ok": pts >= 8}


def grade_incident_duplicate_paid(answer: dict[str, Any], session: ToolSession) -> dict[str, Any]:
    a = _parse_answer(answer)
    blob = json_dumps_lower([a])
    pts = 0
    # root cause: idempotency after outbox / mark processed ordering
    if any(
        t in blob
        for t in (
            "_mark_processed",
            "mark_processed",
            "idempoten",
            "processed_webhook",
        )
    ) and any(t in blob for t in ("outbox", "order_paid", "before", "after", "order")):
        pts += 5
    elif "duplicate" in blob and "webhook" in blob:
        pts += 2
    # function to change
    fix = " | ".join(_as_list(a.get("fix_functions")) + [_norm_sym(a.get("fix_function") or "")]).lower()
    if "handle_payment_webhook" in fix or "payment_service" in fix:
        pts += 3
    ev_s, _, ev_d = _citation_bonus(a, session, ["service/payment_service.py"])
    pts += min(2, ev_s)
    # penalize wrong blame on outbox_worker only
    if "outbox_worker" in blob and "handle_payment_webhook" not in blob and "payment_service" not in blob:
        pts = max(0, pts - 3)
    return {"score": min(10, pts), "max_score": 10, "detail": ev_d, "ok": pts >= 8}


def grade_constrained_idempotency(answer: dict[str, Any], session: ToolSession) -> dict[str, Any]:
    a = _parse_answer(answer)
    files = {_norm_sym(x).replace("fixture/shopapi/", "") for x in _as_list(a.get("touch_files"))}
    # must touch payment_service; may touch webhook_retry; must NOT invent broker package
    pts = 0
    if any("payment_service.py" in f for f in files):
        pts += 3
    if len(files) <= 3 and files:
        pts += 2
    elif len(files) > 3:
        pts += 0
    plan = json_dumps_lower([a])
    if "_mark_processed" in plan or "idempoten" in plan or "processed_webhook" in plan:
        pts += 3
    if "kafka" in plan or "rabbit" in plan or "redis streams" in plan or "new broker" in plan:
        pts = max(0, pts - 4)
    ev_s, _, ev_d = _citation_bonus(a, session, ["service/payment_service.py"])
    pts += min(2, ev_s)
    return {"score": min(10, pts), "max_score": 10, "detail": f"files={sorted(files)}; {ev_d}", "ok": pts >= 8}


def grade_outbox_ack_bug(answer: dict[str, Any], session: ToolSession) -> dict[str, Any]:
    a = _parse_answer(answer)
    blob = json_dumps_lower([a])
    pts = 0
    if "ack" in blob and ("before" in blob or "prior" in blob or "order" in blob):
        pts += 4
    if "process_once" in blob or "outbox_worker" in blob:
        pts += 3
    if "publish" in blob and "ack" in blob:
        pts += 1
    ev_s, _, ev_d = _citation_bonus(a, session, ["worker/outbox_worker.py"])
    pts += min(2, ev_s)
    return {"score": min(10, pts), "max_score": 10, "detail": ev_d, "ok": pts >= 8}


def grade_invariant_doc_vs_code(answer: dict[str, Any], session: ToolSession) -> dict[str, Any]:
    """Which README invariants are violated?"""
    a = _parse_answer(answer)
    violated = {x.upper().replace(" ", "") for x in _as_list(a.get("violated_invariants"))}
    # normalize I2, I3, I4 style
    norm = set()
    for v in _as_list(a.get("violated_invariants")):
        u = v.upper().replace(" ", "")
        if "I2" in u or "TENANT" in u:
            norm.add("I2")
        if "I3" in u or "IDEMPOT" in u or "DOUBLE" in u:
            norm.add("I3")
        if "I4" in u or "CACHE" in u:
            norm.add("I4")
        if "I1" in u or "OUTBOX" in u:
            norm.add("I1")
    # Gold: I2, I3, I4 violated; I1 mostly holds for cancel/paid paths (paid does outbox)
    # I1 is arguably OK for cancel_order. mark_paid does outbox. webhook does outbox.
    # So violated = I2, I3, I4. Claiming I1 is wrong (false positive).
    pts = 0
    for inv in ("I2", "I3", "I4"):
        if inv in norm:
            pts += 2
    if "I1" not in norm:
        pts += 2
    else:
        pts += 0  # false positive
    ev_s, _, ev_d = _citation_bonus(
        a,
        session,
        ["README.md", "service/payment_service.py", "service/invoice_service.py"],
    )
    pts += min(2, ev_s)
    return {
        "score": min(10, pts),
        "max_score": 10,
        "detail": f"violated={sorted(norm)}; {ev_d}",
        "ok": pts >= 8,
    }


def json_dumps_lower(obj: Any) -> str:
    import json

    return json.dumps(obj, default=str).lower()


SYSTEM_PREAMBLE = """You are exploring a small Python service repo called shopapi.
You MUST use tools to inspect the code before answering. Do not invent file paths.

Tools (emit exactly one call at a time using this XML form — note the tag name arch_tool,
NOT tool_call; some servers break on tool_call):
<arch_tool>
{"name": "TOOL_NAME", "arguments": {..}}
</arch_tool>

Available tools:
- list_dir(path="."): list directory
- read_file(path): read file with line numbers
- grep(pattern, path=".", ignore_case=false): regex search
- find_refs(symbol): definitions and references for a symbol

Budget: at most 30 tool calls per task.

When you have enough evidence, finish with ONLY:
<arch_final>
{...JSON matching the task schema...}
</arch_final>

Rules:
- citations must be "relative/path.py:symbol_or_label"
- unsupported guesses score poorly
- no markdown outside the tags once you start tool calls
"""


def build_tasks() -> list[Task]:
    return [
        Task(
            id="chain_delete_order",
            title="Call chain: DELETE /orders/{id}",
            family="call_chain",
            max_score=10,
            required_files=["api/orders.py", "service/order_service.py"],
            prompt=SYSTEM_PREAMBLE
            + """
Task: Trace request handling for DELETE /orders/{id}.

Return JSON:
{
  "chain": ["module.func", ... in call order from HTTP handler to durable effects],
  "side_effects": ["short strings"],
  "citations": ["path.py:symbol", ...]
}
Include auth, service, repo/outbox/cache steps that actually run on success.
""",
            grade=grade_delete_chain,
        ),
        Task(
            id="chain_payment_webhook",
            title="Call chain: POST /webhooks/payment",
            family="call_chain",
            max_score=10,
            required_files=["api/webhooks.py", "service/payment_service.py"],
            prompt=SYSTEM_PREAMBLE
            + """
Task: Trace POST /webhooks/payment for a new (non-duplicate) webhook.

Return JSON:
{
  "chain": ["module.func", ...],
  "side_effects": ["short strings"],
  "citations": ["path.py:symbol", ...]
}
""",
            grade=grade_payment_chain,
        ),
        Task(
            id="tenant_invoice_isolation",
            title="Tenant isolation for invoices",
            family="invariant",
            max_score=10,
            required_files=["service/invoice_service.py", "store/invoice_repo.py"],
            prompt=SYSTEM_PREAMBLE
            + """
Task: README claims I2 — invoice reads are tenant-scoped.
Find where tenant scoping IS enforced for invoices, and every bypass that can return or export another tenant's invoice data.

Return JSON:
{
  "enforced_at": ["module.func or path symbols"],
  "bypasses": ["module.func or path symbols"],
  "citations": ["path.py:symbol", ...]
}
""",
            grade=grade_tenant_invoice,
        ),
        Task(
            id="invariant_doc_vs_code",
            title="Which README invariants fail?",
            family="invariant",
            max_score=10,
            required_files=["README.md", "service/payment_service.py"],
            prompt=SYSTEM_PREAMBLE
            + """
Task: README lists invariants I1–I4. Which are violated by the current code?
Do NOT mark an invariant violated unless you found concrete evidence.

Return JSON:
{
  "violated_invariants": ["I2", "..."],
  "evidence": {"I2": "brief reason", "...": "..."},
  "citations": ["path.py:symbol", ...]
}
""",
            grade=grade_invariant_doc_vs_code,
        ),
        Task(
            id="review_list_orders_n1",
            title="Review: list_orders performance smell",
            family="review",
            max_score=10,
            required_files=["service/order_service.py"],
            prompt=SYSTEM_PREAMBLE
            + """
Task: Architecture review of service/order_service.py list_orders (and callees as needed).
Focus on correctness of auth/tenant handling and performance smells.

Return JSON:
{
  "findings": [{"id": "n_plus_1", "severity": "medium|high|low", "summary": "..."}],
  "max_severity": "medium",
  "auth_ok": true,
  "citations": ["path.py:symbol", ...]
}
Only report real issues. Do not invent auth failures if auth is fine.
""",
            grade=grade_review_n_plus_one,
        ),
        Task(
            id="review_cache_on_paid",
            title="Review: cache vs status change (I4)",
            family="review",
            max_score=10,
            required_files=["service/order_service.py"],
            prompt=SYSTEM_PREAMBLE
            + """
Task: Does mark_paid uphold README invariant I4 (cache invalidated on status change)?

Return JSON:
{
  "i4_holds": false,
  "findings": [{"summary": "...", "severity": "high|medium|low"}],
  "citations": ["path.py:symbol", ...]
}
""",
            grade=grade_review_cache_i4,
        ),
        Task(
            id="incident_duplicate_order_paid",
            title="Incident: duplicate OrderPaid",
            family="incident",
            max_score=10,
            required_files=["service/payment_service.py"],
            prompt=SYSTEM_PREAMBLE
            + """
Task: Symptom: after provider retries, customers sometimes get duplicate OrderPaid outbox events for one webhook_id.
Find the root cause in this repo and the primary function to fix.

Return JSON:
{
  "root_cause": "brief",
  "fix_function": "module.func",
  "fix_functions": ["module.func"],
  "citations": ["path.py:symbol", ...]
}
""",
            grade=grade_incident_duplicate_paid,
        ),
        Task(
            id="incident_outbox_ack_order",
            title="Incident: lost outbox events",
            family="incident",
            max_score=10,
            required_files=["worker/outbox_worker.py"],
            prompt=SYSTEM_PREAMBLE
            + """
Task: Symptom: an outbox event can disappear if the publisher crashes mid-batch.
Find the ordering bug.

Return JSON:
{
  "root_cause": "brief",
  "fix_function": "module.func",
  "citations": ["path.py:symbol", ...]
}
""",
            grade=grade_outbox_ack_bug,
        ),
        Task(
            id="redesign_webhook_idempotency",
            title="Constrained fix: webhook idempotency",
            family="redesign",
            max_score=10,
            required_files=["service/payment_service.py"],
            prompt=SYSTEM_PREAMBLE
            + """
Task: Propose a minimal fix so payment webhooks cannot double-insert OrderPaid.
Constraints: touch at most 3 files; no new message broker; keep HTTP webhook entrypoint.

Return JSON:
{
  "touch_files": ["relative/paths.py"],
  "plan_steps": ["..."],
  "idempotency_change": "what moves before/after what",
  "citations": ["path.py:symbol", ...]
}
""",
            grade=grade_constrained_idempotency,
        ),
    ]


# gold scripted trajectories for selftest (tool plan + final answer)
SELFTEST_TRAJECTORIES: dict[str, dict[str, Any]] = {
    "chain_delete_order": {
        "tools": [
            ("list_dir", {"path": "api"}),
            ("read_file", {"path": "api/orders.py"}),
            ("read_file", {"path": "service/order_service.py"}),
        ],
        "answer": {
            "chain": [
                "api.orders.handle_delete_order",
                "pkg.auth.authenticate",
                "service.order_service.cancel_order",
                "store.order_repo.soft_delete",
                "store.outbox.insert",
                "store.cache.invalidate_order",
            ],
            "side_effects": [
                "soft-delete order",
                "outbox OrderCancelled",
                "invalidate order cache",
            ],
            "citations": [
                "api/orders.py:handle_delete_order",
                "service/order_service.py:cancel_order",
            ],
        },
    },
    "tenant_invoice_isolation": {
        "tools": [
            ("read_file", {"path": "service/invoice_service.py"}),
            ("read_file", {"path": "store/invoice_repo.py"}),
        ],
        "answer": {
            "enforced_at": ["invoice_service.list_invoices", "invoice_repo.list_by_tenant"],
            "bypasses": [
                "invoice_service.get_invoice",
                "invoice_repo.get_by_id",
                "invoice_service.admin_export_invoices",
                "invoice_repo.export_all",
            ],
            "citations": [
                "service/invoice_service.py:get_invoice",
                "store/invoice_repo.py:get_by_id",
            ],
        },
    },
    "incident_duplicate_order_paid": {
        "tools": [
            ("grep", {"pattern": "OrderPaid|_mark_processed|webhook"}),
            ("read_file", {"path": "service/payment_service.py"}),
        ],
        "answer": {
            "root_cause": "outbox OrderPaid inserted before _mark_processed; retry duplicates",
            "fix_function": "service.payment_service.handle_payment_webhook",
            "fix_functions": ["service.payment_service.handle_payment_webhook"],
            "citations": ["service/payment_service.py:handle_payment_webhook"],
        },
    },
    "chain_payment_webhook": {
        "tools": [
            ("read_file", {"path": "api/webhooks.py"}),
            ("read_file", {"path": "service/payment_service.py"}),
            ("find_refs", {"symbol": "mark_paid"}),
        ],
        "answer": {
            "chain": [
                "api.webhooks.handle_payment_webhook",
                "service.payment_service.handle_payment_webhook",
                "store.outbox.insert",
                "service.order_service.mark_paid",
                "service.payment_service._mark_processed",
            ],
            "side_effects": ["write payments row", "outbox OrderPaid", "mark order paid"],
            "citations": [
                "api/webhooks.py:handle_payment_webhook",
                "service/payment_service.py:handle_payment_webhook",
            ],
        },
    },
    "invariant_doc_vs_code": {
        "tools": [
            ("read_file", {"path": "README.md"}),
            ("read_file", {"path": "service/payment_service.py"}),
            ("read_file", {"path": "service/invoice_service.py"}),
            ("read_file", {"path": "service/order_service.py"}),
        ],
        "answer": {
            "violated_invariants": ["I2", "I3", "I4"],
            "evidence": {
                "I2": "get_invoice and admin_export bypass tenant scope",
                "I3": "OrderPaid before _mark_processed",
                "I4": "mark_paid skips cache invalidate",
            },
            "citations": [
                "README.md:I2",
                "service/payment_service.py:handle_payment_webhook",
                "service/invoice_service.py:get_invoice",
            ],
        },
    },
    "review_list_orders_n1": {
        "tools": [("read_file", {"path": "service/order_service.py"})],
        "answer": {
            "findings": [
                {
                    "id": "n_plus_1",
                    "severity": "medium",
                    "summary": "list_orders calls get_order per row",
                }
            ],
            "max_severity": "medium",
            "auth_ok": True,
            "citations": ["service/order_service.py:list_orders"],
        },
    },
    "review_cache_on_paid": {
        "tools": [("read_file", {"path": "service/order_service.py"})],
        "answer": {
            "i4_holds": False,
            "findings": [
                {
                    "summary": "mark_paid updates status but missing cache invalidate",
                    "severity": "high",
                }
            ],
            "citations": ["service/order_service.py:mark_paid"],
        },
    },
    "incident_outbox_ack_order": {
        "tools": [("read_file", {"path": "worker/outbox_worker.py"})],
        "answer": {
            "root_cause": "process_once acks outbox row before publish",
            "fix_function": "worker.outbox_worker.process_once",
            "citations": ["worker/outbox_worker.py:process_once"],
        },
    },
    "redesign_webhook_idempotency": {
        "tools": [("read_file", {"path": "service/payment_service.py"})],
        "answer": {
            "touch_files": ["service/payment_service.py"],
            "plan_steps": [
                "record processed_webhooks before outbox insert",
                "or use transactional outbox with unique webhook_id",
            ],
            "idempotency_change": "move _mark_processed before outbox.insert/order_paid",
            "citations": ["service/payment_service.py:handle_payment_webhook"],
        },
    },
}
