"""Deterministic conjecture-generation fixture supply."""

from __future__ import annotations

from lemma.supply.types import TaskCandidate, fixture_candidate


def fixture_candidates() -> tuple[TaskCandidate, ...]:
    return (
        fixture_candidate(
            slug="nat_self_eq",
            source_stream="conjecture_generated",
            source_name="conjecture-fixture",
            theorem_name="conjecture_nat_self_eq",
            type_expr="∀ n : Nat, n = n",
            queue_depth=2,
        ),
    )
