"""Domain-neutral verifier adapter contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class VerificationResult:
    accepted: bool
    verifier_id: str
    verifier_version: str
    domain_id: str
    stdout: str = ""
    stderr: str = ""
    error_type: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


class VerifierAdapter(ABC):
    domain_id: str
    verifier_id: str

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings

    @abstractmethod
    def verify(self, task: dict[str, Any] | Any, submission: dict[str, Any] | Any) -> VerificationResult:
        """Return deterministic verifier acceptance for one task/submission pair."""

    @abstractmethod
    def normalize_artifact(
        self,
        task: dict[str, Any] | Any,
        submission: dict[str, Any] | Any,
        result: VerificationResult,
    ) -> dict[str, Any]:
        """Return the accepted artifact shape for corpus rows."""

    @abstractmethod
    def task_schema(self) -> dict[str, Any]:
        """Return the task schema this adapter expects."""

    @abstractmethod
    def submission_schema(self) -> dict[str, Any]:
        """Return the submission schema this adapter expects."""
