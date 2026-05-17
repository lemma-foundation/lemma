"""Submission packages for proof-task attempts."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lemma.tasks import LemmaTask


def proof_sha256(proof_script: str) -> str:
    """Return the canonical script hash used by v1 deduplication."""
    return hashlib.sha256(proof_script.encode("utf-8")).hexdigest()


class LemmaSubmission(BaseModel):
    """A miner's proof attempt for one exact task version."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    task_id: str
    target_sha256: str
    solver_hotkey: str
    proof_script: str
    proof_sha256: str = ""
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    signature: str | None = None

    @model_validator(mode="after")
    def _validate_hashes(self) -> LemmaSubmission:
        if self.schema_version != 1:
            raise ValueError("submission schema_version must be 1")
        expected = proof_sha256(self.proof_script)
        if self.proof_sha256 and self.proof_sha256.lower() != expected:
            raise ValueError(f"proof_sha256 mismatch: got {expected}, expected {self.proof_sha256}")
        self.proof_sha256 = expected
        return self


def build_submission(
    task: LemmaTask,
    *,
    solver_hotkey: str,
    proof_script: str,
    created_at: str | None = None,
    metadata: dict[str, Any] | None = None,
    signature: str | None = None,
) -> LemmaSubmission:
    """Build a task-bound submission package."""
    return LemmaSubmission(
        task_id=task.id,
        target_sha256=task.target_sha256,
        solver_hotkey=solver_hotkey,
        proof_script=proof_script,
        proof_sha256=proof_sha256(proof_script),
        created_at=created_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        metadata=metadata or {},
        signature=signature,
    )
