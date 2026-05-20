"""Domain-neutral corpus row v2 helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lemma.graph import build_dependencies, build_row_graph
from lemma.lean.proof_identity import proof_identity
from lemma.lean.sandbox import VerifyResult
from lemma.license import license_state_for
from lemma.quality import build_row_quality
from lemma.submissions import LemmaSubmission
from lemma.tasks import LemmaTask
from lemma.verifiers.base import VerificationResult


class CorpusRowV2(BaseModel):
    """One accepted verified theorem/proof row."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 2
    row_id: str = ""
    task_id: str
    domain_id: str
    verifier_id: str
    verifier_version: str
    task_type: str
    prompt: dict[str, Any]
    accepted_artifact: dict[str, Any]
    verification: dict[str, Any]
    provenance: dict[str, Any]
    dependencies: dict[str, Any] = Field(default_factory=dict)
    graph: dict[str, Any] = Field(default_factory=dict)
    license: str = "CC-BY-4.0"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_row(self) -> CorpusRowV2:
        if self.schema_version != 2:
            raise ValueError("corpus row schema_version must be 2")
        if self.verification.get("accepted") is not True:
            raise ValueError("corpus row v2 requires an accepted verifier result")
        expected = row_id_for_v2(
            domain_id=self.domain_id,
            task_id=self.task_id,
            normalized_artifact_hash=normalized_artifact_hash(self.accepted_artifact),
        )
        if self.row_id and self.row_id != expected:
            raise ValueError(f"row_id mismatch: got {expected}, expected {self.row_id}")
        self.row_id = expected
        return self


def stable_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalized_artifact_hash(artifact: dict[str, Any]) -> str:
    return sha256_text(stable_json(artifact))


def task_hash(prompt: dict[str, Any]) -> str:
    return sha256_text(stable_json(prompt))


def row_id_for_v2(*, domain_id: str, task_id: str, normalized_artifact_hash: str) -> str:
    return sha256_text("\n".join([domain_id, task_id, normalized_artifact_hash]))


def build_corpus_row_v2(
    task: LemmaTask,
    submission: LemmaSubmission,
    result: VerificationResult | VerifyResult,
    *,
    validator_hotkey: str,
    block: int = 0,
    timestamp: str | None = None,
    repo_commit: str = "",
    rewarded: bool = True,
) -> CorpusRowV2:
    """Build the canonical v2 row for an accepted Lean proof."""
    accepted, verifier_id, verifier_version, stdout, stderr, metrics = _result_parts(task, result)
    identity = proof_identity(
        proof_sha256=submission.proof_sha256,
        proof_term_hash=str(metrics.get("proof_term_hash") or "") or None,
        structural_fingerprint=str(metrics.get("structural_fingerprint") or "") or None,
        proof_script=submission.proof_script,
    )
    prompt = task.to_v2()["prompt"]
    artifact = {
        "proof": submission.proof_script,
        "imports": list(task.imports),
        "full_file": submission.proof_script,
        "proof_sha256": submission.proof_sha256,
        "proof_term_hash": identity.proof_term_hash,
        "structural_fingerprint": metrics.get("structural_fingerprint") or None,
        "proof_identity": identity.value,
        "proof_identity_source": identity.source,
        "proof_identity_strength": identity.strength,
    }
    dependencies = build_dependencies(task)
    license_state = license_state_for(task.source_license, str(task.metadata.get("license_state") or ""))
    quality = build_row_quality(
        triviality_checked=task.triviality_status != "unknown" or bool(task.metadata.get("triviality_checked")),
        baseline_solvers_failed=not bool(task.metadata.get("baseline_solved")),
        difficulty_band=task.difficulty_band,
        near_duplicate_score=float(task.metadata.get("near_duplicate_score") or 0.0),
        dependency_depth=dependencies.dependency_depth,
        license_state=license_state,
        proof_identity_strength=identity.strength,
        model_lift_release=task.metadata.get("model_lift_release"),
    )
    return CorpusRowV2(
        task_id=task.id,
        domain_id=task.domain_id,
        verifier_id=verifier_id,
        verifier_version=verifier_version,
        task_type="theorem_proving",
        prompt=prompt,
        accepted_artifact=artifact,
        verification={
            "accepted": accepted,
            "stdout_hash": sha256_text(stdout),
            "stderr_hash": sha256_text(stderr),
            "metrics": metrics,
        },
        provenance={
            "miner_hotkey": submission.solver_hotkey,
            "validator_hotkey": validator_hotkey,
            "block": block,
            "timestamp": timestamp or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "repo_commit": repo_commit,
        },
        dependencies=dependencies.model_dump(),
        graph=build_row_graph(
            task=task,
            proof_identity=identity.value,
            proof_sha256=submission.proof_sha256,
            solver_hotkey=submission.solver_hotkey,
            validator_hotkey=validator_hotkey,
        ).model_dump(),
        metadata={
            "task_hash": task_hash(prompt),
            "normalized_artifact_hash": normalized_artifact_hash(artifact),
            "rewarded": rewarded,
            "full_reward_eligible": rewarded and identity.strength == "strong" and quality.useful_verified_row,
            "quality": quality.model_dump(),
            "source": task.source_stream,
            "source_license": task.source_license,
        },
    )


def _result_parts(
    task: LemmaTask,
    result: VerificationResult | VerifyResult,
) -> tuple[bool, str, str, str, str, dict[str, Any]]:
    if isinstance(result, VerificationResult):
        return (
            result.accepted,
            result.verifier_id,
            result.verifier_version,
            result.stdout,
            result.stderr,
            result.metrics,
        )
    return (
        result.passed,
        task.verifier_id,
        task.verifier_version,
        result.stdout_tail,
        result.stderr_tail,
        {
            "build_seconds": result.build_seconds,
            "proof_term_hash": result.proof_term_hash,
            "legacy_reason": result.reason,
        },
    )
