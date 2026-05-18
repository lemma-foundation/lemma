"""Lean domain scoring policy."""

from __future__ import annotations

from typing import Any

from lemma.verifiers.base import VerificationResult


def lean_artifact_credit(task: dict[str, Any], submission: dict[str, Any], result: VerificationResult) -> int:  # noqa: ARG001
    """Lean v1 awards one credit only after deterministic verifier acceptance."""
    return 1 if result.accepted else 0
