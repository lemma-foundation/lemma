"""Public snapshot of the active proof tasks."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lemma.common.config import LemmaSettings
from lemma.tasks import LemmaTask, TaskRegistry


class CurrentProblem(BaseModel):
    """One public-safe active task row for website dashboards."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    task_version: int = Field(ge=1)
    title: str
    theorem_name: str
    type_expr: str
    statement: str
    source_stream: str
    source_ref: dict[str, Any]
    source_license: str
    difficulty_band: str
    queue_position: int | None = Field(default=None, ge=0)
    queue_depth: int = Field(ge=0)
    frontier_depth: int | None = Field(default=None, ge=0)
    target_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    lean_toolchain: str
    mathlib_rev: str
    policy: str


class CurrentProblemsSnapshot(BaseModel):
    """Public-safe active task snapshot for `lemmasub.net`."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    generated_at: str
    registry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    registry_task_count: int = Field(ge=0)
    active_K: int = Field(ge=1)
    tempo: int = Field(ge=0)
    active_tempo_seconds: int = Field(ge=1)
    active_seed_mode: Literal["static", "epoch_randomness"] = "static"
    active_epoch_randomness_source: Literal["manual", "chain_drand"] = "manual"
    active_epoch_randomness_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    active_selection_seed_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    frontier_depth: int = Field(ge=0)
    active_queue_seed: str
    task_count: int = Field(ge=0)
    tasks: tuple[CurrentProblem, ...]


def _timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _problem(task: LemmaTask) -> CurrentProblem:
    return CurrentProblem(
        task_id=task.id,
        task_version=task.task_version,
        title=task.title or task.theorem_name,
        theorem_name=task.theorem_name,
        type_expr=task.type_expr,
        statement=task.statement,
        source_stream=task.source_stream,
        source_ref=task.source_ref.model_dump(exclude_none=True),
        source_license=task.source_license,
        difficulty_band=task.difficulty_band,
        queue_position=task.queue_position,
        queue_depth=task.queue_depth,
        frontier_depth=task.frontier_depth,
        target_sha256=task.target_sha256,
        lean_toolchain=task.lean_toolchain,
        mathlib_rev=task.mathlib_rev,
        policy=task.policy,
    )


def build_current_problems_snapshot(
    settings: LemmaSettings,
    *,
    registry: TaskRegistry | None = None,
    generated_at: str | None = None,
    tempo: int | None = None,
) -> CurrentProblemsSnapshot:
    """Build the public active-task snapshot without proof or operator state."""
    from lemma.protocol_invariants import enforce_production_invariants
    from lemma.validator import (
        active_epoch_randomness_sha256,
        active_selection_seed_sha256,
        active_tasks_for_validation,
        current_active_tempo,
        curriculum_controlled_settings,
        task_registry_for_validation,
    )

    active_tempo = current_active_tempo(settings) if tempo is None else tempo
    effective_settings = curriculum_controlled_settings(settings, tempo=active_tempo)
    task_registry = registry or task_registry_for_validation(effective_settings, tempo=active_tempo)
    enforce_production_invariants(settings, task_registry)
    active_tasks = active_tasks_for_validation(task_registry, effective_settings, tempo=active_tempo)
    tasks = tuple(_problem(task) for task in active_tasks)
    return CurrentProblemsSnapshot(
        schema_version=1,
        generated_at=generated_at or _timestamp(),
        registry_sha256=task_registry.sha256,
        registry_task_count=len(task_registry.tasks),
        active_K=effective_settings.active_task_count,
        tempo=active_tempo,
        active_tempo_seconds=effective_settings.active_tempo_seconds,
        active_seed_mode=effective_settings.active_seed_mode,
        active_epoch_randomness_source=effective_settings.active_epoch_randomness_source,
        active_epoch_randomness_sha256=active_epoch_randomness_sha256(effective_settings, tempo=active_tempo),
        active_selection_seed_sha256=active_selection_seed_sha256(
            task_registry, effective_settings, tempo=active_tempo
        ),
        frontier_depth=effective_settings.frontier_depth,
        active_queue_seed=effective_settings.active_queue_seed,
        task_count=len(tasks),
        tasks=tasks,
    )


def write_current_problems_snapshot(path: Path, snapshot: CurrentProblemsSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = snapshot.model_dump(mode="json", exclude_none=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
