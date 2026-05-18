"""Experimental Verus verifier adapter stub."""

from __future__ import annotations

from typing import Any

from lemma.common.config import LemmaSettings
from lemma.verifiers.base import VerificationResult, VerifierAdapter


class VerusVerifierAdapter(VerifierAdapter):
    domain_id = "verus"
    verifier_id = "verus"

    def __init__(self, settings: LemmaSettings | None = None) -> None:
        self.settings: LemmaSettings = settings or LemmaSettings()

    def verify(self, task: dict[str, Any] | Any, submission: dict[str, Any] | Any) -> VerificationResult:  # noqa: ARG002
        raise NotImplementedError("Verus domain is experimental and disabled by default")

    def normalize_artifact(
        self,
        task: dict[str, Any] | Any,
        submission: dict[str, Any] | Any,
        result: VerificationResult,
    ) -> dict[str, Any]:  # noqa: ARG002
        return {
            "rust_code": "",
            "spec": "",
            "proof_annotations": "",
        }

    def task_schema(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "domain_id": self.domain_id,
            "task_type": "rust_function_verification",
            "prompt": {
                "function_signature": "string",
                "specification": "string",
                "tests": [],
                "allowed_imports": [],
            },
        }

    def submission_schema(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "domain_id": self.domain_id,
            "artifact": {
                "rust_code": "string",
                "spec": "string",
                "proof_annotations": "string",
            },
        }
