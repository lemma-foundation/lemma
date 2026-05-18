"""Domain scoring registry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from lemma.scoring.lean import lean_artifact_credit
from lemma.verifiers.base import VerificationResult

ScoreFn = Callable[[dict[str, Any], dict[str, Any], VerificationResult], int]

SCORE_FUNCTIONS: dict[str, ScoreFn] = {
    "lean": lean_artifact_credit,
}


def compute_domain_score(
    domain_id: str,
    task: dict[str, Any],
    submission: dict[str, Any],
    result: VerificationResult,
) -> int:
    try:
        score_fn = SCORE_FUNCTIONS[domain_id]
    except KeyError as e:
        raise ValueError(f"Unknown domain_id: {domain_id}") from e
    return score_fn(task, submission, result)
