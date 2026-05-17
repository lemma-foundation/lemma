"""JSONL corpus rows and replay for accepted Lean proofs."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lemma.common.config import LemmaSettings
from lemma.lean.sandbox import VerifyResult
from lemma.lean.verify_runner import run_lean_verify
from lemma.submissions import LemmaSubmission
from lemma.tasks import LemmaTask


class VerificationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    verifier_version: str = "lemma-lean-v1"
    elapsed_ms: int | None = None
    reason: str | None = None


class CorpusRow(BaseModel):
    """One replayable accepted proof row."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    task_id: str
    theorem_name: str
    type_expr: str
    statement: str
    imports: tuple[str, ...]
    lean_toolchain: str
    mathlib_rev: str
    policy: str = "restricted_helpers"
    target_sha256: str
    axiom_set: list[str] = Field(default_factory=list)
    proof_script: str
    proof_sha256: str
    proof_term_hash: str | None = None
    solver_hotkey: str
    epoch: int | None = None
    tempo: int | None = None
    source_stream: str
    verified_at: str
    verification: VerificationSummary
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_version(self) -> CorpusRow:
        if self.schema_version != 1:
            raise ValueError("corpus row schema_version must be 1")
        return self

    def to_task(self) -> LemmaTask:
        stub = "\n".join(
            [
                *(f"import {name}" for name in self.imports),
                "",
                "namespace Submission",
                "",
                f"theorem {self.theorem_name} : {self.type_expr} := by",
                "  sorry",
                "",
                "end Submission",
                "",
            ]
        )
        return LemmaTask(
            id=self.task_id,
            title=str(self.metadata.get("title") or self.task_id),
            source_stream=self.source_stream,  # type: ignore[arg-type]
            imports=self.imports,
            theorem_name=self.theorem_name,
            type_expr=self.type_expr,
            statement=self.statement,
            submission_stub=stub,
            lean_toolchain=self.lean_toolchain,
            mathlib_rev=self.mathlib_rev,
            policy=self.policy,
            target_sha256=self.target_sha256,
            metadata=dict(self.metadata),
        )


def build_corpus_row(
    task: LemmaTask,
    submission: LemmaSubmission,
    result: VerifyResult,
    *,
    epoch: int | None = None,
    tempo: int | None = None,
    proof_term_hash: str | None = None,
    verified_at: str | None = None,
) -> CorpusRow:
    """Create a replayable corpus row from an accepted submission."""
    return CorpusRow(
        task_id=task.id,
        theorem_name=task.theorem_name,
        type_expr=task.type_expr,
        statement=task.statement,
        imports=task.imports,
        lean_toolchain=task.lean_toolchain,
        mathlib_rev=task.mathlib_rev,
        policy=task.policy,
        target_sha256=task.target_sha256,
        proof_script=submission.proof_script,
        proof_sha256=submission.proof_sha256,
        proof_term_hash=proof_term_hash,
        solver_hotkey=submission.solver_hotkey,
        epoch=epoch,
        tempo=tempo,
        source_stream=task.source_stream,
        verified_at=verified_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        verification=VerificationSummary(
            passed=result.passed,
            reason=None if result.passed else result.reason,
            elapsed_ms=int(result.build_seconds * 1000) if result.build_seconds else None,
        ),
        metadata={"title": task.title, **task.metadata},
    )


def write_jsonl(rows: Iterable[CorpusRow], path: Path) -> None:
    path.write_text(
        "".join(row.model_dump_json(exclude_none=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[CorpusRow]:
    rows: list[CorpusRow] = []
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(CorpusRow.model_validate_json(line))
        except ValueError as e:
            raise ValueError(f"{path}:{no}: invalid corpus row: {e}") from e
    return rows


def validate_jsonl(path: Path) -> int:
    return len(read_jsonl(path))


def replay_jsonl(settings: LemmaSettings, path: Path) -> list[VerifyResult]:
    results: list[VerifyResult] = []
    for row in read_jsonl(path):
        task = row.to_task()
        results.append(
            run_lean_verify(
                settings,
                verify_timeout_s=settings.lean_verify_timeout_s,
                problem=task.to_problem(),
                proof_script=row.proof_script,
                submission_policy=row.policy,
            ),
        )
    return results
