"""Deterministic proof-state graph fixture supply."""

from __future__ import annotations

from lemma.supply.types import TaskCandidate, fixture_candidate


def fixture_candidates() -> tuple[TaskCandidate, ...]:
    return (
        fixture_candidate(
            slug="intermediate_true",
            source_stream="state_graph",
            source_name="state-graph-fixture",
            theorem_name="state_graph_intermediate_true",
            type_expr="True",
            queue_depth=1,
            metadata={"state_graph_depth": 1},
        ),
    )
