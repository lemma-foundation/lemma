"""Mathlib snapshot proof-erasure supply tests."""

from __future__ import annotations

import json

from lemma.supply.mathlib_snapshot import MathlibSnapshotRow, candidate_from_row, candidates_from_jsonl


def test_mathlib_snapshot_row_becomes_proof_erased_candidate() -> None:
    row = MathlibSnapshotRow(
        theorem_name="Nat.zero_add",
        type_expr="∀ n : Nat, 0 + n = n",
        imports=("Mathlib.Data.Nat.Basic",),
        mathlib_rev="abc123",
        source_path="Mathlib/Data/Nat/Basic.lean",
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
        },
        {
            "theorem_name": "Eq.refl",
            "type_expr": "∀ a : Nat, a = a",
            "mathlib_rev": "abc123",
            "source_path": "Mathlib/Init.lean",
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    candidates = candidates_from_jsonl(path, limit=1)

    assert len(candidates) == 1
    assert candidates[0].theorem_name == "True.intro"
