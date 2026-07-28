"""Claim scoring helper."""

from __future__ import annotations


def score_claims(correct: int, n_claims: int, evidence_bonus: int) -> dict[str, int | bool]:
    ev = max(0, evidence_bonus)
    score = correct + ev
    failed = correct != n_claims or score < n_claims
    return {
        "correct": correct,
        "score": score,
        "max_score": n_claims + 3,
        "ok": not failed and correct >= int(0.8 * n_claims),
    }
