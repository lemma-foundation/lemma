"""Mathlib snapshot proof-erasure supply tests."""

from __future__ import annotations

import json

import pytest
from lemma.supply.mathlib_snapshot import (
    MathlibSnapshotRow,
    candidate_from_row,
    candidates_from_jsonl,
    rows_from_jsonl,
    snapshot_quality_summary,
)
from lemma.supply.types import registry_tasks_from_candidates
from pydantic import ValidationError


def test_mathlib_snapshot_row_becomes_proof_erased_candidate() -> None:
    row = MathlibSnapshotRow(
        theorem_name="Nat.zero_add",
        type_expr="∀ n : Nat, 0 + n = n",
        imports=("Mathlib.Data.Nat.Basic",),
        mathlib_rev="abc123",
        source_path="Mathlib/Data/Nat/Basic.lean",
        source_license="Apache-2.0",
        source_line=12,
        proof_sha256="f" * 64,
        queue_depth=2,
    )

    candidate = candidate_from_row(row)
    task = candidate.to_task(queue_position=7, frontier_depth=2)

    assert candidate.id == "lemma.mathlib_snapshot.Nat.zero_add"
    assert candidate.source_stream == "mathlib_snapshot"
    assert candidate.source_ref.commit == "abc123"
    assert "sorry" in candidate.statement
    assert candidate.submission_stub.startswith("import Mathlib.Data.Nat.Basic\n")
    assert "erased_proof_sha256" in candidate.metadata
    assert task.queue_position == 7
    assert task.queue_depth == 2


def test_mathlib_snapshot_jsonl_loader_is_deterministic(tmp_path) -> None:
    path = tmp_path / "snapshot.jsonl"
    rows = [
        {
            "theorem_name": "True.intro",
            "type_expr": "True",
            "mathlib_rev": "abc123",
            "source_path": "Mathlib/Init.lean",
            "source_license": "Apache-2.0",
        },
        {
            "theorem_name": "Eq.refl",
            "type_expr": "∀ a : Nat, a = a",
            "mathlib_rev": "abc123",
            "source_path": "Mathlib/Init.lean",
            "source_license": "Apache-2.0",
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    candidates = candidates_from_jsonl(path, limit=1)

    assert len(candidates) == 1
    assert candidates[0].theorem_name == "True.intro"


def test_mathlib_snapshot_quality_summary_reports_depth_and_signal_coverage(tmp_path) -> None:
    path = tmp_path / "snapshot.jsonl"
    rows = [
        {
            "theorem_name": "Easy.target",
            "type_expr": "True",
            "mathlib_rev": "abc123",
            "source_path": "Mathlib/Easy.lean",
            "source_license": "Apache-2.0",
            "queue_depth": 0,
        },
        {
            "theorem_name": "Frontier.target",
            "type_expr": "True",
            "mathlib_rev": "abc123",
            "source_path": "Mathlib/Frontier.lean",
            "source_license": "Apache-2.0",
            "queue_depth": 7,
            "difficulty_score": 9,
            "citation_weight": 12.5,
            "direct_dependency_count": 8,
            "dependency_depth": 14,
            "transitive_dependency_hash": "a" * 64,
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    summary = snapshot_quality_summary(rows_from_jsonl(path))

    assert summary["rows"] == 2
    assert summary["queue_depth_counts"] == {"0": 1, "7": 1}
    assert summary["difficulty_band_counts"] == {"easy": 1, "frontier": 1}
    assert summary["frontier_rows"] == 1
    assert summary["max_signal"] == {
        "citation_weight": 12.5,
        "dependency_depth": 14,
        "direct_dependency_count": 8,
    }
    assert summary["metadata_coverage"] == {
        "baseline_solved": 0,
        "citation_weight": 1,
        "dependency_depth": 1,
        "direct_dependency_count": 1,
        "transitive_dependency_hash": 1,
    }


def test_mathlib_snapshot_candidates_become_deterministic_registry_tasks() -> None:
    rows = (
        MathlibSnapshotRow(
            theorem_name="Deep.target",
            type_expr="True",
            mathlib_rev="abc123",
            source_path="Mathlib/Deep.lean",
            source_license="Apache-2.0",
            queue_depth=3,
        ),
        MathlibSnapshotRow(
            theorem_name="Shallow.target",
            type_expr="True",
            mathlib_rev="abc123",
            source_path="Mathlib/Shallow.lean",
            source_license="Apache-2.0",
            queue_depth=0,
        ),
    )
    candidates = tuple(candidate_from_row(row) for row in rows)

    tasks = registry_tasks_from_candidates(candidates, seed="pytest", frontier_depth=2)

    assert [task.queue_position for task in tasks] == [0, 1]
    assert tasks[0].theorem_name == "Shallow.target"
    assert all(task.frontier_depth == 2 for task in tasks)


@pytest.mark.parametrize(
    "patch",
    [
        {"mathlib_rev": ""},
        {"source_path": ""},
        {"source_license": ""},
        {"theorem_name": "bad theorem name"},
        {"theorem_name": "!!!"},
        {"queue_depth": -1},
    ],
)
def test_mathlib_snapshot_row_rejects_bad_contract_fields(patch: dict[str, object]) -> None:
    row: dict[str, object] = {
        "theorem_name": "Good.name",
        "type_expr": "True",
        "mathlib_rev": "abc123",
        "source_path": "Mathlib/Good.lean",
        "source_license": "Apache-2.0",
    }
    row.update(patch)

    with pytest.raises(ValidationError):
        MathlibSnapshotRow.model_validate(row)


def test_mathlib_snapshot_row_requires_explicit_source_license() -> None:
    row: dict[str, object] = {
        "theorem_name": "Good.name",
        "type_expr": "True",
        "mathlib_rev": "abc123",
        "source_path": "Mathlib/Good.lean",
    }

    with pytest.raises(ValidationError):
        MathlibSnapshotRow.model_validate(row)


@pytest.mark.parametrize("source_path", ["/Mathlib/Good.lean", "Mathlib/../Good.lean", "Mathlib/Good.txt"])
def test_mathlib_snapshot_row_rejects_non_replayable_source_paths(source_path: str) -> None:
    with pytest.raises(ValidationError):
        MathlibSnapshotRow(
            theorem_name="Good.name",
            type_expr="True",
            mathlib_rev="abc123",
            source_path=source_path,
            source_license="Apache-2.0",
        )
