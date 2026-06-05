"""Launch-readiness assessment for the real-task pivot.

This package runs a deterministic, end-to-end check that the validator, scoring,
selection, Proof Atlas, and site surfaces hold together without manual rescue.
It evaluates the roadmap's launch criteria into a structured report. It is pure,
offline tooling: no network, no chain, no live mining, and no model calls.
"""

from __future__ import annotations

from lemma.launch.readiness import (
    CriterionResult,
    LaunchReport,
    assess_launch_readiness,
)

__all__ = [
    "CriterionResult",
    "LaunchReport",
    "assess_launch_readiness",
]
