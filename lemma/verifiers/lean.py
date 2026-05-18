"""Lean verifier adapter."""

from __future__ import annotations

from typing import Any, cast

from lemma.common.config import LemmaSettings
from lemma.lean.sandbox import VerifyReason, VerifyResult
from lemma.lean.verify_runner import run_lean_verify
from lemma.submissions import LemmaSubmission
from lemma.tasks import LEAN_DOMAIN_ID, LEAN_VERIFIER_ID, LemmaTask, SourceRef
from lemma.verifiers.base import VerificationResult, VerifierAdapter


class LeanVerifierAdapter(VerifierAdapter):
    domain_id = LEAN_DOMAIN_ID
    verifier_id = LEAN_VERIFIER_ID

    def __init__(self, settings: LemmaSettings | None = None) -> None:
        self.settings: LemmaSettings = settings or LemmaSettings()

    def verify(
        self,
        task: dict[str, Any] | LemmaTask,
        submission: dict[str, Any] | LemmaSubmission,
    ) -> VerificationResult:
        lean_task = _coerce_task(task)
        lean_submission = _coerce_submission(submission)
        result = run_lean_verify(
            self.settings,
            verify_timeout_s=self.settings.lean_verify_timeout_s,
            problem=lean_task.to_problem(),
            proof_script=lean_submission.proof_script,
            submission_policy=lean_task.policy,
        )
        return VerificationResult(
            accepted=result.passed,
            verifier_id=self.verifier_id,
            verifier_version=lean_task.verifier_version,
            domain_id=self.domain_id,
            stdout=result.stdout_tail,
            stderr=result.stderr_tail,
            error_type=None if result.passed else result.reason,
            metrics={
                "build_seconds": result.build_seconds,
                "proof_sha256": lean_submission.proof_sha256,
                "proof_term_hash": result.proof_term_hash,
                "legacy_reason": result.reason,
            },
        )

    def normalize_artifact(
        self,
        task: dict[str, Any] | LemmaTask,
        submission: dict[str, Any] | LemmaSubmission,
        result: VerificationResult,
    ) -> dict[str, Any]:
        lean_task = _coerce_task(task)
        lean_submission = _coerce_submission(submission)
        return {
            "proof": lean_submission.proof_script,
            "imports": list(lean_task.imports),
            "full_file": lean_submission.proof_script,
            "proof_sha256": lean_submission.proof_sha256,
            "proof_term_hash": str(result.metrics.get("proof_term_hash") or ""),
        }

    def task_schema(self) -> dict[str, Any]:
        return {"schema_version": 2, "domain_id": self.domain_id, "task_type": "theorem_proving"}

    def submission_schema(self) -> dict[str, Any]:
        return {"schema_version": 2, "domain_id": self.domain_id, "artifact": {"proof": "string"}}


def verify_result_from_adapter_result(result: VerificationResult) -> VerifyResult:
    """Bridge the domain-neutral adapter result back to the current Lean score path."""
    return VerifyResult(
        passed=result.accepted,
        reason=_legacy_reason(result),
        stderr_tail=result.stderr,
        stdout_tail=result.stdout,
        build_seconds=float(result.metrics.get("build_seconds") or 0.0),
        proof_term_hash=str(result.metrics.get("proof_term_hash") or ""),
    )


def _coerce_task(task: dict[str, Any] | LemmaTask) -> LemmaTask:
    if isinstance(task, LemmaTask):
        return task
    if int(task.get("schema_version", 1)) == 2:
        return _legacy_task_from_v2(task)
    return LemmaTask.model_validate(task)


def _coerce_submission(submission: dict[str, Any] | LemmaSubmission) -> LemmaSubmission:
    if isinstance(submission, LemmaSubmission):
        return submission
    return LemmaSubmission.model_validate(submission)


def _legacy_task_from_v2(task: dict[str, Any]) -> LemmaTask:
    prompt_raw = task.get("prompt")
    constraints_raw = task.get("constraints")
    metadata_raw = task.get("metadata")
    prompt = cast(dict[str, Any], prompt_raw) if isinstance(prompt_raw, dict) else {}
    constraints = cast(dict[str, Any], constraints_raw) if isinstance(constraints_raw, dict) else {}
    metadata = cast(dict[str, Any], metadata_raw) if isinstance(metadata_raw, dict) else {}
    source_ref_data = (
        cast(dict[str, Any], metadata.get("source_ref"))
        if isinstance(metadata.get("source_ref"), dict)
        else {"kind": "v2", "name": str(task["source"])}
    )
    return LemmaTask(
        id=str(task["task_id"]),
        task_version=int(metadata.get("task_version") or 1),
        title=str(metadata.get("title") or task["task_id"]),
        source_stream=str(task.get("source") or "generated"),  # type: ignore[arg-type]
        source_ref=SourceRef.model_validate(source_ref_data),
        source_license=str(metadata.get("source_license") or "CC-BY-4.0"),
        imports=tuple(prompt.get("imports") or ("Mathlib",)),
        theorem_name=str(prompt["theorem_name"]),
        type_expr=str(prompt.get("type_expr") or "True"),
        statement=str(prompt["statement"]),
        submission_stub=str(prompt.get("submission_stub") or prompt["statement"]),
        lean_toolchain=str(constraints.get("lean_toolchain") or "leanprover/lean4:v4.30.0-rc2"),
        mathlib_rev=str(constraints.get("mathlib_rev") or "unknown"),
        policy=str(constraints.get("policy") or "restricted_helpers"),
        target_sha256=str(constraints.get("target_sha256") or ""),
        metadata={key: value for key, value in metadata.items() if key not in {"source_ref", "source_license"}},
    )


def _legacy_reason(result: VerificationResult) -> VerifyReason:
    if result.accepted:
        return "ok"
    raw = str(result.error_type or "compile_error")
    allowed: set[VerifyReason] = {
        "compile_error",
        "axiom_violation",
        "cheat_token",
        "policy_violation",
        "timeout",
        "oom",
        "docker_error",
        "remote_error",
    }
    return cast(VerifyReason, raw) if raw in allowed else "compile_error"
