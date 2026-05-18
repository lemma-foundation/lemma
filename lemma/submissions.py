"""Submission packages for proof-task attempts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lemma.tasks import LEAN_DOMAIN_ID, LEAN_VERIFIER_ID, LEAN_VERIFIER_VERSION, LemmaTask

MAX_PROOF_CHARS = 200_000


def proof_sha256(proof_script: str) -> str:
    """Return the canonical script hash used by v1 deduplication."""
    return hashlib.sha256(proof_script.encode("utf-8")).hexdigest()


def canonical_submission_payload(data: dict[str, Any]) -> str:
    """Stable signing payload for live miner responses."""
    fields = {
        "schema_version": int(data["schema_version"]),
        "task_id": str(data["task_id"]),
        "task_version": int(data["task_version"]),
        "target_sha256": str(data["target_sha256"]),
        "solver_hotkey": str(data["solver_hotkey"]),
        "proof_sha256": str(data["proof_sha256"]),
        "created_at": str(data["created_at"]),
    }
    return json.dumps(fields, sort_keys=True, separators=(",", ":"))


def signature_payload_sha256(data: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_submission_payload(data).encode("utf-8")).hexdigest()


class LemmaSubmission(BaseModel):
    """A miner's proof attempt for one exact task version."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    task_id: str
    task_version: int = Field(default=1, ge=1)
    target_sha256: str
    solver_hotkey: str
    proof_script: str
    proof_sha256: str = ""
    created_at: str
    timelock_ciphertext: str | None = None
    drand_round: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    signature: str | None = None
    signature_payload_sha256: str = ""

    @field_validator("proof_script")
    @classmethod
    def _proof_size(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("proof_script is empty")
        if len(value) > MAX_PROOF_CHARS:
            raise ValueError(f"proof_script exceeds {MAX_PROOF_CHARS} characters")
        return value

    @model_validator(mode="after")
    def _validate_hashes(self) -> LemmaSubmission:
        if self.schema_version != 1:
            raise ValueError("submission schema_version must be 1")
        expected = proof_sha256(self.proof_script)
        if self.proof_sha256 and self.proof_sha256.lower() != expected:
            raise ValueError(f"proof_sha256 mismatch: got {expected}, expected {self.proof_sha256}")
        self.proof_sha256 = expected
        data = self.model_dump()
        expected_payload_hash = signature_payload_sha256(data)
        if self.signature_payload_sha256 and self.signature_payload_sha256.lower() != expected_payload_hash:
            raise ValueError(
                "signature_payload_sha256 mismatch: "
                f"got {expected_payload_hash}, expected {self.signature_payload_sha256}"
            )
        self.signature_payload_sha256 = expected_payload_hash
        return self

    @property
    def is_signed(self) -> bool:
        return bool((self.signature or "").strip())


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
        task_version=task.task_version,
        target_sha256=task.target_sha256,
        solver_hotkey=solver_hotkey,
        proof_script=proof_script,
        proof_sha256=proof_sha256(proof_script),
        created_at=created_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        metadata=metadata or {},
        signature=signature,
    )


def submission_v2_from_lean_submission(submission: LemmaSubmission, task: LemmaTask | None = None) -> dict[str, Any]:
    """Return the domain-neutral submission row for a legacy Lean proof."""
    domain_id = task.domain_id if task else LEAN_DOMAIN_ID
    verifier_id = task.verifier_id if task else LEAN_VERIFIER_ID
    verifier_version = task.verifier_version if task else LEAN_VERIFIER_VERSION
    imports = list(task.imports) if task else []
    created_at_block = int(submission.metadata.get("created_at_block") or 0)
    return {
        "schema_version": 2,
        "task_id": submission.task_id,
        "domain_id": domain_id,
        "miner_hotkey": submission.solver_hotkey,
        "artifact": {
            "proof": submission.proof_script,
            "imports": imports,
            "full_file": submission.proof_script,
            "proof_sha256": submission.proof_sha256,
        },
        "created_at_block": created_at_block,
        "declared_verifier_id": verifier_id,
        "declared_verifier_version": verifier_version,
        "metadata": {
            "task_version": submission.task_version,
            "target_sha256": submission.target_sha256,
            **submission.metadata,
        },
    }


def validate_submission_for_task(
    submission: LemmaSubmission,
    task: LemmaTask,
    *,
    require_signature: bool = False,
) -> None:
    """Reject attempts that are not bound to the exact active task."""
    if submission.task_id != task.id:
        raise ValueError(f"submission task_id mismatch: {submission.task_id} != {task.id}")
    if submission.task_version != task.task_version:
        raise ValueError(f"submission task_version mismatch: {submission.task_version} != {task.task_version}")
    if submission.target_sha256 != task.target_sha256:
        raise ValueError("submission target_sha256 mismatch")
    if require_signature and not submission.is_signed:
        raise ValueError("live miner submission is unsigned")
