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

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from lemma.supply.types import TaskCandidate, fixture_candidate, lean_stub
from lemma.task_supply import DEFAULT_TOOLCHAIN
from lemma.tasks import SourceRef

_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")
_LEAN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_HEX_SHA256 = re.compile(r"[0-9a-fA-F]{64}")


def _required_text(value: str, field: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{field} must be non-empty")
    return text


class MathlibSnapshotRow(BaseModel):
    """One theorem statement exported from a pinned Mathlib checkout."""

    model_config = ConfigDict(extra="forbid")

    theorem_name: str
    type_expr: str
    imports: tuple[str, ...] = ("Mathlib",)
    mathlib_rev: str
    source_path: str
    source_line: int | None = Field(default=None, ge=1)
    source_license: str
    proof_sha256: str | None = None
    queue_depth: int = Field(default=0, ge=0)

    @field_validator("theorem_name")
    @classmethod
    def _validate_theorem_name(cls, value: str) -> str:
        text = _required_text(value, "theorem_name")
        if not _LEAN_NAME.fullmatch(text):
            raise ValueError("theorem_name must be an ASCII dotted Lean identifier")
        return text

    @field_validator("type_expr", "mathlib_rev", "source_license")
    @classmethod
    def _validate_required_text(cls, value: str, info: ValidationInfo) -> str:
        return _required_text(value, info.field_name or "value")

    @field_validator("imports")
    @classmethod
    def _validate_imports(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("imports must contain at least one module")
        cleaned = tuple(_required_text(item, "imports") for item in value)
        if any(not _LEAN_NAME.fullmatch(item) for item in cleaned):
            raise ValueError("imports must be ASCII dotted Lean module names")
        return cleaned

    @field_validator("mathlib_rev")
    @classmethod
    def _validate_mathlib_rev(cls, value: str) -> str:
        text = _required_text(value, "mathlib_rev")
        if any(ch.isspace() for ch in text):
            raise ValueError("mathlib_rev must not contain whitespace")
        return text

    @field_validator("source_path")
    @classmethod
    def _validate_source_path(cls, value: str) -> str:
        text = _required_text(value, "source_path")
        parts = text.split("/")
        if text.startswith("/") or "\\" in text or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("source_path must be a repo-relative Lean path")
        if not text.endswith(".lean"):
            raise ValueError("source_path must end with .lean")
        return text

    @field_validator("proof_sha256")
    @classmethod
    def _validate_proof_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = _required_text(value, "proof_sha256")
        if not _HEX_SHA256.fullmatch(text):
            raise ValueError("proof_sha256 must be 64 hex characters")
        return text.lower()


def _task_id(theorem_name: str) -> str:
    slug = _SAFE_ID.sub("_", theorem_name.strip()).strip("._-")
    if not slug:
        raise ValueError("theorem_name does not contain a safe task id slug")
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
