"""Deterministic hard-target variant fixture supply."""

from __future__ import annotations

from lemma.supply.types import TaskCandidate, fixture_candidate


def fixture_candidates(stalled_task_id: str = "lemma.hard.target") -> tuple[TaskCandidate, ...]:
    return (
        fixture_candidate(
            slug="hard_target_true_variant",
            source_stream="hard_target_variant",
            source_name="hard-target-variant-fixture",
            theorem_name="hard_target_true_variant",
            type_expr="True",
            queue_depth=1,
            metadata={"variant_of": stalled_task_id, "variant_role": "scaffold"},
        ),
    )
