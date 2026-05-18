"""JSONL corpus rows and replay for accepted Lean proofs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lemma.common.config import LemmaSettings
from lemma.lean.sandbox import VerifyResult
from lemma.lean.verify_runner import run_lean_verify
from lemma.submissions import LemmaSubmission
from lemma.tasks import LemmaTask, SourceRef


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
    row_id: str = ""
    task_id: str
    task_version: int
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
    proof_identity: str = ""
    proof_identity_source: str = "proof_sha256_fallback"
    solver_hotkey: str
    validator_hotkey: str
    epoch: int | None = None
    tempo: int | None = None
    active_K: int | None = None
    queue_position: int | None = None
    queue_depth: int | None = None
    frontier_depth: int | None = None
    ema_solve_rate: float | None = None
    source_stream: str
    source_ref: SourceRef
    source_license: str
    accepted_at: str
    rewarded: bool
    verification: VerificationSummary
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_row(self) -> CorpusRow:
        if self.schema_version != 1:
            raise ValueError("corpus row schema_version must be 1")
        if not self.verification.passed:
            raise ValueError("failed proofs are not corpus rows")
        if not self.proof_identity:
            self.proof_identity = self.proof_term_hash or self.proof_sha256
        expected = row_id_for(
            target_sha256=self.target_sha256,
            proof_sha256=self.proof_sha256,
            solver_hotkey=self.solver_hotkey,
            validator_hotkey=self.validator_hotkey,
        )
        if self.row_id and self.row_id != expected:
            raise ValueError(f"row_id mismatch: got {expected}, expected {self.row_id}")
        self.row_id = expected
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
            task_version=self.task_version,
            title=str(self.metadata.get("title") or self.task_id),
            source_stream=self.source_stream,  # type: ignore[arg-type]
            source_ref=self.source_ref,
            source_license=self.source_license,
            imports=self.imports,
            theorem_name=self.theorem_name,
            type_expr=self.type_expr,
            statement=self.statement,
            submission_stub=stub,
            lean_toolchain=self.lean_toolchain,
            mathlib_rev=self.mathlib_rev,
            policy=self.policy,
            target_sha256=self.target_sha256,
            queue_position=self.queue_position,
            queue_depth=self.queue_depth or 0,
            frontier_depth=self.frontier_depth,
            metadata=dict(self.metadata),
        )


def row_id_for(*, target_sha256: str, proof_sha256: str, solver_hotkey: str, validator_hotkey: str) -> str:
    payload = "\n".join([target_sha256, proof_sha256, solver_hotkey, validator_hotkey])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _public_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    blocked_keys = ("path", "file", "dir", "log", "workspace", "secret", "token", "key")
    local_user_prefix = "/" + "Users/"
    root_ssh = "root" + "@"
    out: dict[str, Any] = {}
    for key, value in metadata.items():
        lower = key.lower()
        if any(part in lower for part in blocked_keys):
            continue
        if isinstance(value, str) and (local_user_prefix in value or root_ssh in value):
            continue
        out[key] = value
    return out


def build_corpus_row(
    task: LemmaTask,
    submission: LemmaSubmission,
    result: VerifyResult,
    *,
    validator_hotkey: str,
    rewarded: bool,
    epoch: int | None = None,
    tempo: int | None = None,
    proof_term_hash: str | None = None,
    accepted_at: str | None = None,
    axiom_set: list[str] | None = None,
    active_K: int | None = None,
    ema_solve_rate: float | None = None,
    proof_identity_source: str = "proof_sha256_fallback",
) -> CorpusRow:
    """Create a replayable corpus row from an accepted submission."""
    term_hash = proof_term_hash or result.proof_term_hash
    identity = term_hash or submission.proof_sha256
    source = proof_identity_source if term_hash is None else "lean_proof_term"
    return CorpusRow(
        task_id=task.id,
        task_version=task.task_version,
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
        proof_term_hash=term_hash,
        proof_identity=identity,
        proof_identity_source=source,
        solver_hotkey=submission.solver_hotkey,
        validator_hotkey=validator_hotkey,
        epoch=epoch,
        tempo=tempo,
        active_K=active_K,
        queue_position=task.queue_position,
        queue_depth=task.queue_depth,
        frontier_depth=task.frontier_depth,
        ema_solve_rate=ema_solve_rate,
        source_stream=task.source_stream,
        source_ref=task.source_ref,
        source_license=task.source_license,
        accepted_at=accepted_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        rewarded=rewarded,
        axiom_set=axiom_set or [],
        verification=VerificationSummary(
            passed=result.passed,
            reason=None if result.passed else result.reason,
            elapsed_ms=int(result.build_seconds * 1000) if result.build_seconds else None,
        ),
        metadata={"title": task.title, **_public_metadata(task.metadata)},
    )


def write_jsonl(rows: Iterable[CorpusRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def build_corpus_index(corpus_dir: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    row_count = 0
    for path in sorted(corpus_dir.glob("*.jsonl")):
        raw = path.read_bytes()
        rows = read_jsonl(path)
        row_count += len(rows)
        files.append(
            {
                "path": path.name,
                "rows": len(rows),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    return {
        "schema_version": 1,
        "row_count": row_count,
        "files": files,
        "updated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def write_corpus_index(corpus_dir: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(build_corpus_index(corpus_dir), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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
