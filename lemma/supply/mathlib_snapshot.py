"""Mathlib snapshot proof-erasure supply.

This module consumes a deterministic manifest produced by an off-chain Mathlib
extractor. Validators can typecheck and queue the resulting task artifacts
without running extraction or model inference in the scoring path.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from lemma.supply.types import TaskCandidate, fixture_candidate, lean_stub
from lemma.task_supply import DEFAULT_TOOLCHAIN
from lemma.tasks import SourceRef

_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")


class MathlibSnapshotRow(BaseModel):
    """One theorem statement exported from a pinned Mathlib checkout."""

    model_config = ConfigDict(extra="forbid")

    theorem_name: str
    type_expr: str
    imports: tuple[str, ...] = ("Mathlib",)
    mathlib_rev: str
    source_path: str
    source_line: int | None = Field(default=None, ge=1)
    source_license: str = "Apache-2.0"
    proof_sha256: str | None = None
    queue_depth: int = Field(default=0, ge=0)


def _task_id(theorem_name: str) -> str:
    slug = _SAFE_ID.sub("_", theorem_name.strip()).strip("._-")
    return f"lemma.mathlib_snapshot.{slug}"


def candidate_from_row(row: MathlibSnapshotRow) -> TaskCandidate:
    theorem_name = row.theorem_name.strip()
    type_expr = row.type_expr.strip()
    source_ref = SourceRef(
        kind="mathlib",
        name=theorem_name,
        commit=row.mathlib_rev,
        path=row.source_path,
    )
    metadata: dict[str, object] = {}
    if row.source_line is not None:
        metadata["source_line"] = row.source_line
    if row.proof_sha256:
        metadata["erased_proof_sha256"] = row.proof_sha256
    return TaskCandidate(
        id=_task_id(theorem_name),
        title=theorem_name,
        source_stream="mathlib_snapshot",
        source_ref=source_ref,
        source_license=row.source_license,
        imports=row.imports,
        theorem_name=theorem_name,
        type_expr=type_expr,
        statement=f"theorem {theorem_name} : {type_expr} := by\n  sorry",
        submission_stub=lean_stub(theorem_name, type_expr),
        lean_toolchain=DEFAULT_TOOLCHAIN,
        mathlib_rev=row.mathlib_rev,
        queue_depth=row.queue_depth,
        metadata=metadata,
    )


def candidates_from_rows(rows: Iterable[MathlibSnapshotRow]) -> tuple[TaskCandidate, ...]:
    return tuple(candidate_from_row(row) for row in rows)


def candidates_from_jsonl(path: Path, *, limit: int | None = None) -> tuple[TaskCandidate, ...]:
    out: list[TaskCandidate] = []
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = MathlibSnapshotRow.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"{path}:{no}: invalid Mathlib snapshot row: {e}") from e
        out.append(candidate_from_row(row))
        if limit is not None and len(out) >= limit:
            break
    return tuple(out)


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
