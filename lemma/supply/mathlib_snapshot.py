"""Deterministic Mathlib snapshot fixture supply."""

from __future__ import annotations

from lemma.supply.types import TaskCandidate, fixture_candidate


def fixture_candidates() -> tuple[TaskCandidate, ...]:
    return (
        fixture_candidate(
            slug="true_intro",
            source_stream="mathlib_snapshot",
            source_name="mathlib-fixture",
            theorem_name="mathlib_snapshot_true_intro",
            type_expr="True",
            queue_depth=0,
        ),
    )
