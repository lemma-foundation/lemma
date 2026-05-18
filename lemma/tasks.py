"""Training-task registry for Lean proof data."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lemma.common.config import LemmaSettings
from lemma.problems.base import Problem

SourceStream = Literal[
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
                "queue_position": self.queue_position,
                "queue_depth": self.queue_depth,
                "frontier_depth": self.frontier_depth,
                "triviality_status": self.triviality_status,
                **self.metadata,
            },
        )


@dataclass(frozen=True)
class TaskRegistry:
    schema_version: int
    tasks: tuple[LemmaTask, ...]
    sha256: str
    signed_by: str | None = None
    signature: str | None = None
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


def load_task_registry(raw: bytes, expected_sha256: str | None = None) -> TaskRegistry:
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
    try:
        tasks = tuple(LemmaTask.model_validate(row) for row in rows)
    except ValueError as e:
        raise TaskError(str(e)) from e
    return TaskRegistry(
        schema_version=1,
        tasks=tasks,
        sha256=digest,
        signed_by=payload.get("signed_by") if isinstance(payload.get("signed_by"), str) else None,
        signature=payload.get("signature") if isinstance(payload.get("signature"), str) else None,
        created_at=payload.get("created_at") if isinstance(payload.get("created_at"), str) else None,
    )


def fetch_task_registry(settings: LemmaSettings) -> TaskRegistry:
    raw = _read_registry_bytes(settings.task_registry_url, float(settings.task_http_timeout_s))
    return load_task_registry(raw, settings.task_registry_sha256_expected)
