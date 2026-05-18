"""Deterministic auto-formalization fixture supply."""

from __future__ import annotations

from lemma.supply.types import TaskCandidate, fixture_candidate


def fixture_candidates() -> tuple[TaskCandidate, ...]:
    return (
        fixture_candidate(
            slug="nl_true_statement",
            source_stream="auto_formalized",
            source_name="auto-formalization-fixture",
            theorem_name="auto_formalized_true_statement",
            type_expr="True",
            queue_depth=2,
            metadata={"natural_language_source": "A proposition that is always true."},
        ),
    )
