"""Training-task registry for Lean proof data."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, Protocol
from urllib.parse import unquote, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lemma.common.config import LemmaSettings
from lemma.problems.base import Problem

LEAN_DOMAIN_ID: Final[Literal["lean"]] = "lean"
LEAN_VERIFIER_ID: Final = "lake-build"
LEAN_VERIFIER_VERSION: Final = "lemma-lean-v1"

SourceStream = Literal[
    "procedural",
    "mathlib_snapshot",
    "mathlib_perturbation",
    "state_graph",
    "auto_formalized",
    "conjecture_generated",
    "hard_target_variant",
    "trivial_curriculum",
    "generated",
    "proof_repair",
    "theorem_variant",
    "premise_limited",
    "benchmark_practice",
    "human_curated",
]


class TaskError(RuntimeError):
    """Raised when the task registry or a task row is invalid."""


RegistrySignatureStatus = Literal["unsigned", "metadata_only", "verified"]


class RegistrySignatureVerifier(Protocol):
    """Optional registry-signature verifier.

    The production validator path trusts registry byte hashes. Signature
    verification is explicit so metadata fields cannot silently become trust.
    """

    def verify_registry(self, *, raw: bytes, signed_by: str, signature: str) -> bool:
        """Return True when the registry signature is accepted."""
        ...


def registry_signing_payload(payload: dict[str, Any]) -> bytes:
    """Canonical bytes signed by registry authorities.

    The published registry file can be pretty-printed and SHA-pinned exactly as
    written. The signature covers the registry content with signature metadata
    removed, so attaching or rotating `signed_by` / `signature` does not change
    the message being verified.
    """
    unsigned = {key: value for key, value in payload.items() if key not in {"signed_by", "signature"}}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class Ss58RegistrySignatureVerifier:
    """Verify registry signatures against an SS58 public hotkey address."""

    def verify_registry(self, *, raw: bytes, signed_by: str, signature: str) -> bool:
        from bittensor_wallet import Keypair

        return bool(Keypair(ss58_address=signed_by).verify(raw, signature))


class SourceRef(BaseModel):
    """Public provenance for a task source."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    name: str
    url: str | None = None
    commit: str | None = None
    path: str | None = None


def _normalize_sha256(value: str | None) -> str | None:
    raw = (value or "").strip().lower()
    if raw.startswith("sha256:"):
        raw = raw.removeprefix("sha256:")
    return raw or None


def problem_target_sha256(problem: Problem) -> str:
    """Hash the verifier-owned target source exactly as Lean sees it."""
    return hashlib.sha256(problem.challenge_source().encode("utf-8")).hexdigest()


class LemmaTask(BaseModel):
    """One exact Lean theorem task miners can prove."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    id: str
    task_version: int = Field(default=1, ge=1)
    domain_id: Literal["lean"] = LEAN_DOMAIN_ID
    verifier_id: str = LEAN_VERIFIER_ID
    verifier_version: str = LEAN_VERIFIER_VERSION
    title: str = ""
    source_stream: SourceStream = "generated"
    source_ref: SourceRef
    source_license: str
    imports: tuple[str, ...] = ("Mathlib",)
    theorem_name: str
    type_expr: str
    statement: str
    submission_stub: str
    lean_toolchain: str
    mathlib_rev: str
    policy: str = "restricted_helpers"
    target_sha256: str = ""
    queue_position: int | None = Field(default=None, ge=0)
    queue_depth: int = Field(default=0, ge=0)
    frontier_depth: int | None = Field(default=None, ge=0)
    triviality_status: Literal["unknown", "trivial_curriculum", "paid_easy", "paid_medium", "paid_frontier"] = "unknown"
    activation_status: Literal["paid", "curriculum", "benchmark", "quarantine", "rejected"] = "paid"
    difficulty_band: Literal["easy", "medium", "hard", "frontier"] = "easy"
    active_epoch: int | None = None
    expires_epoch: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_target_hash(self) -> LemmaTask:
        if self.schema_version != 1:
            raise ValueError("task schema_version must be 1")
        expected = problem_target_sha256(self.to_problem())
        pinned = _normalize_sha256(self.target_sha256)
        if pinned and pinned != expected:
            raise ValueError(f"target_sha256 mismatch: got {expected}, expected {pinned}")
        self.target_sha256 = expected
        return self

    def to_problem(self) -> Problem:
        return Problem(
            id=self.id,
            theorem_name=self.theorem_name,
            type_expr=self.type_expr,
            split=self.source_stream,
            lean_toolchain=self.lean_toolchain,
            mathlib_rev=self.mathlib_rev,
            imports=self.imports,
            extra={
                "challenge_full": self.statement,
                "submission_stub": self.submission_stub,
                "submission_policy": self.policy,
                "source_stream": self.source_stream,
                "source_ref": self.source_ref.model_dump(exclude_none=True),
                "source_license": self.source_license,
                "task_version": self.task_version,
                "domain_id": self.domain_id,
                "verifier_id": self.verifier_id,
                "verifier_version": self.verifier_version,
                "queue_position": self.queue_position,
                "queue_depth": self.queue_depth,
                "frontier_depth": self.frontier_depth,
                "triviality_status": self.triviality_status,
                "activation_status": self.activation_status,
                "difficulty_band": self.difficulty_band,
                **self.metadata,
            },
        )

    def to_v2(self) -> dict[str, Any]:
        """Return the domain-neutral task row used by schema v2 exports."""
        created_at_block = self.active_epoch or int(self.metadata.get("created_at_block") or 0)
        return {
            "schema_version": 2,
            "task_id": self.id,
            "domain_id": self.domain_id,
            "verifier_id": self.verifier_id,
            "verifier_version": self.verifier_version,
            "task_type": "theorem_proving",
            "created_at_block": created_at_block,
            "source": self.source_stream,
            "prompt": {
                "theorem_name": self.theorem_name,
                "imports": list(self.imports),
                "statement": self.statement,
                "type_expr": self.type_expr,
                "submission_stub": self.submission_stub,
            },
            "constraints": {
                "policy": self.policy,
                "lean_toolchain": self.lean_toolchain,
                "mathlib_rev": self.mathlib_rev,
                "target_sha256": self.target_sha256,
            },
            "scoring": {
                "rule": "first_valid_unique_verified_artifact",
                "reward_unit": "proof_unit",
                "denominator": "active_slot_weight_sum",
            },
            "metadata": {
                "task_version": self.task_version,
                "title": self.title,
                "source_ref": self.source_ref.model_dump(exclude_none=True),
                "source_license": self.source_license,
                "queue_position": self.queue_position,
                "queue_depth": self.queue_depth,
                "frontier_depth": self.frontier_depth,
                "triviality_status": self.triviality_status,
                "activation_status": self.activation_status,
                "difficulty_band": self.difficulty_band,
                **self.metadata,
            },
        }


def upgrade_task_v1_to_v2(old_task: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a legacy Lean task row into the domain-neutral v2 shape."""
    return LemmaTask.model_validate(old_task).to_v2()


@dataclass(frozen=True)
class TaskRegistry:
    schema_version: int
    tasks: tuple[LemmaTask, ...]
    sha256: str
    signed_by: str | None = None
    signature: str | None = None
    signature_status: RegistrySignatureStatus = "unsigned"
    created_at: str | None = None

    def get(self, task_id: str) -> LemmaTask:
        wanted = task_id.strip()
        for task in self.tasks:
            if task.id == wanted:
                return task
        raise TaskError(f"unknown task id: {task_id}")


def _read_registry_bytes(source: str, timeout_s: float) -> bytes:
    src = source.strip()
    if not src:
        raise TaskError("LEMMA_TASK_REGISTRY_URL is empty")
    if src.startswith(("http://", "https://")):
        try:
            response = httpx.get(src, timeout=timeout_s, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise TaskError(f"could not fetch task registry: {e}") from e
        return response.content
    if src.startswith("file://"):
        parsed = urlparse(src)
        path = Path(unquote(parsed.path))
    else:
        path = Path(src).expanduser()
    try:
        return path.read_bytes()
    except OSError as e:
        raise TaskError(f"could not read task registry {path}: {e}") from e


def _signature_metadata(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    signed_by_raw = payload.get("signed_by")
    signature_raw = payload.get("signature")
    signed_by = signed_by_raw.strip() if isinstance(signed_by_raw, str) and signed_by_raw.strip() else None
    signature = signature_raw.strip() if isinstance(signature_raw, str) and signature_raw.strip() else None
    if (signed_by is None) != (signature is None):
        raise TaskError("task registry signed_by and signature must be provided together")
    return signed_by, signature


def _signature_status(
    *,
    raw: bytes,
    signed_by: str | None,
    signature: str | None,
    verifier: RegistrySignatureVerifier | None,
) -> RegistrySignatureStatus:
    if signed_by is None or signature is None:
        return "unsigned"
    if verifier is None:
        return "metadata_only"
    try:
        accepted = verifier.verify_registry(raw=raw, signed_by=signed_by, signature=signature)
    except TaskError:
        raise
    except Exception as e:
        raise TaskError(f"task registry signature verification failed: {e}") from e
    if not accepted:
        raise TaskError("task registry signature verification failed")
    return "verified"


def load_task_registry(
    raw: bytes,
    expected_sha256: str | None = None,
    *,
    signature_verifier: RegistrySignatureVerifier | None = None,
) -> TaskRegistry:
    digest = hashlib.sha256(raw).hexdigest()
    expected = _normalize_sha256(expected_sha256)
    if expected and digest != expected:
        raise TaskError(f"task registry sha256 mismatch: got {digest}, expected {expected}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise TaskError(f"task registry is not valid UTF-8 JSON: {e}") from e
    if int(payload.get("schema_version", 0)) != 1:
        raise TaskError("task registry schema_version must be 1")
    rows = payload.get("tasks")
    if not isinstance(rows, list):
        raise TaskError("task registry must contain a tasks list")
    signed_by, signature = _signature_metadata(payload)
    signature_status = _signature_status(
        raw=registry_signing_payload(payload),
        signed_by=signed_by,
        signature=signature,
        verifier=signature_verifier,
    )
    try:
        tasks = tuple(LemmaTask.model_validate(row) for row in rows)
    except ValueError as e:
        raise TaskError(str(e)) from e
    return TaskRegistry(
        schema_version=1,
        tasks=tasks,
        sha256=digest,
        signed_by=signed_by,
        signature=signature,
        signature_status=signature_status,
        created_at=payload.get("created_at") if isinstance(payload.get("created_at"), str) else None,
    )


def task_registry_from_tasks(tasks: tuple[LemmaTask, ...]) -> TaskRegistry:
    """Build an in-memory registry from deterministically generated task rows."""
    payload: dict[str, object] = {
        "schema_version": 1,
        "tasks": [task.model_dump(mode="json", exclude_none=True) for task in tasks],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return load_task_registry(raw)


def fetch_task_registry(settings: LemmaSettings, *, verify_signature: bool | None = None) -> TaskRegistry:
    raw = _read_registry_bytes(settings.task_registry_url, float(settings.task_http_timeout_s))
    should_verify = settings.verify_registry_signatures if verify_signature is None else verify_signature
    verifier = Ss58RegistrySignatureVerifier() if should_verify else None
    return load_task_registry(raw, settings.task_registry_sha256_expected, signature_verifier=verifier)
