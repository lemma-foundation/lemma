"""Deterministic Mathlib perturbation fixture supply."""

from __future__ import annotations

from lemma.supply.types import TaskCandidate, fixture_candidate


def fixture_candidates() -> tuple[TaskCandidate, ...]:
    return (
        fixture_candidate(
            slug="and_true_left",
            source_stream="mathlib_perturbation",
            source_name="mathlib-perturbation-fixture",
            theorem_name="perturbation_and_true_left",
            type_expr="True -> True ∧ True",
            queue_depth=1,
            metadata={"variant_of": "mathlib_snapshot_true_intro"},
        ),
    )
