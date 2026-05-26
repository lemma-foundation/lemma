"""Public snapshot of the active proof tasks."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, NamedTuple

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
    active_tempo_source: Literal["wall_clock", "chain"] = "wall_clock"
    active_tempo_seconds: int = Field(ge=1)
    active_window_blocks: int = Field(ge=1)
    active_tempo_blocks: int | None = Field(default=None, ge=1)
    epoch_start_block: int | None = Field(default=None, ge=0)
    next_epoch_start_block: int | None = Field(default=None, ge=0)
    epoch_started_at: str | None = None
    estimated_next_epoch_starts_at: str | None = None
    active_seed_mode: Literal["static", "epoch_randomness"] = "static"
    active_epoch_randomness_source: Literal["manual", "chain_drand"] = "manual"
    active_epoch_randomness_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    active_selection_seed_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    frontier_depth: int = Field(ge=0)
    validator_capacity: int = Field(default=0, ge=0)
    cost_budget_s: float = Field(default=0.0, ge=0.0)
    base_task_cost_s: float = Field(default=0.0, ge=0.0)
    depth_cost_multiplier: float = Field(default=2.0, ge=1.0)
    cost_limited_K: int | None = Field(default=None, ge=1)
    estimated_task_cost_s: float | None = Field(default=None, ge=0.0)
    active_queue_seed: str
    task_count: int = Field(ge=0)
    tasks: tuple[CurrentProblem, ...]


class ChainEpochMetadata(NamedTuple):
    active_tempo_blocks: int | None = None
    epoch_start_block: int | None = None
    next_epoch_start_block: int | None = None
    epoch_started_at: str | None = None
    estimated_next_epoch_starts_at: str | None = None


class CurrentProblemsMetadata(NamedTuple):
    cost_limited_K: int | None
    estimated_task_cost_s: float | None


def _timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp_from_unix(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _chain_epoch_metadata(settings: LemmaSettings, tempo: int) -> ChainEpochMetadata:
    if settings.active_tempo_source != "chain" or settings.active_epoch_randomness_source != "chain_drand":
        return ChainEpochMetadata()
    from lemma.chain.epoch_randomness import resolve_chain_drand_epoch_randomness

    randomness = resolve_chain_drand_epoch_randomness(settings, tempo=tempo)
    block_time_seconds = settings.active_tempo_seconds / max(1, settings.active_window_blocks)
    estimated_next_timestamp = randomness.anchor_block_timestamp + round(randomness.tempo_length * block_time_seconds)
    return ChainEpochMetadata(
        active_tempo_blocks=randomness.tempo_length,
        epoch_start_block=randomness.anchor_block,
        next_epoch_start_block=randomness.anchor_block + randomness.tempo_length,
        epoch_started_at=_timestamp_from_unix(randomness.anchor_block_timestamp),
        estimated_next_epoch_starts_at=_timestamp_from_unix(estimated_next_timestamp),
    )


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


def _curriculum_metadata(settings: LemmaSettings) -> CurrentProblemsMetadata:
    from lemma.supply.controller import CurriculumConfig, cost_limited_k, estimated_task_cost_s

    config = CurriculumConfig(
        beta=settings.curriculum_beta,
        low_band=settings.curriculum_low_band,
        high_band=settings.curriculum_high_band,
        k_min=settings.curriculum_k_min,
        k_max=settings.curriculum_k_max,
        cost_budget_s=settings.curriculum_cost_budget_s,
        base_task_cost_s=settings.curriculum_base_task_cost_s,
        depth_cost_multiplier=settings.curriculum_depth_cost_multiplier,
        window_base_blocks=settings.curriculum_window_base_blocks,
        window_max_blocks=settings.curriculum_window_max_blocks,
        window_depth_multiplier=settings.curriculum_window_depth_multiplier,
        window_k_reference=settings.curriculum_window_k_reference,
    )
    return CurrentProblemsMetadata(
        cost_limited_K=cost_limited_k(settings.frontier_depth, config),
        estimated_task_cost_s=estimated_task_cost_s(settings.frontier_depth, config),
    )


def build_empty_current_problems_snapshot(
    settings: LemmaSettings,
    *,
    generated_at: str | None = None,
    tempo: int | None = None,
) -> CurrentProblemsSnapshot:
    """Build a current-epoch placeholder when the active registry is still unavailable."""
    from lemma.validator import current_active_tempo, curriculum_controlled_settings

    active_tempo = current_active_tempo(settings) if tempo is None else tempo
    effective_settings = curriculum_controlled_settings(settings, tempo=active_tempo)
    epoch_metadata = _chain_epoch_metadata(effective_settings, active_tempo)
    curriculum_metadata = _curriculum_metadata(effective_settings)
    return CurrentProblemsSnapshot(
        schema_version=1,
        generated_at=generated_at or _timestamp(),
        registry_sha256="0" * 64,
        registry_task_count=0,
        active_K=effective_settings.active_task_count,
        tempo=active_tempo,
        active_tempo_source=effective_settings.active_tempo_source,
        active_tempo_seconds=effective_settings.active_tempo_seconds,
        active_window_blocks=effective_settings.active_window_blocks,
        active_tempo_blocks=epoch_metadata.active_tempo_blocks,
        epoch_start_block=epoch_metadata.epoch_start_block,
        next_epoch_start_block=epoch_metadata.next_epoch_start_block,
        epoch_started_at=epoch_metadata.epoch_started_at,
        estimated_next_epoch_starts_at=epoch_metadata.estimated_next_epoch_starts_at,
        active_seed_mode=effective_settings.active_seed_mode,
        active_epoch_randomness_source=effective_settings.active_epoch_randomness_source,
        frontier_depth=effective_settings.frontier_depth,
        validator_capacity=effective_settings.validator_capacity,
        cost_budget_s=effective_settings.curriculum_cost_budget_s,
        base_task_cost_s=effective_settings.curriculum_base_task_cost_s,
        depth_cost_multiplier=effective_settings.curriculum_depth_cost_multiplier,
        cost_limited_K=curriculum_metadata.cost_limited_K,
        estimated_task_cost_s=curriculum_metadata.estimated_task_cost_s,
        active_queue_seed=effective_settings.active_queue_seed,
        task_count=0,
        tasks=(),
    )


def build_current_problems_snapshot(
    settings: LemmaSettings,
    *,
    registry: TaskRegistry | None = None,
    registry_is_active: bool = False,
    generated_at: str | None = None,
    tempo: int | None = None,
    include_randomness_hashes: bool = True,
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
    epoch_metadata = _chain_epoch_metadata(effective_settings, active_tempo)
    curriculum_metadata = _curriculum_metadata(effective_settings)
    enforce_production_invariants(settings, task_registry)
    active_tasks = (
        tuple(
            task.model_copy(update={"queue_position": position, "frontier_depth": effective_settings.frontier_depth})
            for position, task in enumerate(task_registry.tasks[: effective_settings.active_task_count])
        )
        if registry_is_active
        else active_tasks_for_validation(task_registry, effective_settings, tempo=active_tempo)
    )
    tasks = tuple(_problem(task) for task in active_tasks)
    return CurrentProblemsSnapshot(
        schema_version=1,
        generated_at=generated_at or _timestamp(),
        registry_sha256=task_registry.sha256,
        registry_task_count=len(task_registry.tasks),
        active_K=effective_settings.active_task_count,
        tempo=active_tempo,
        active_tempo_source=effective_settings.active_tempo_source,
        active_tempo_seconds=effective_settings.active_tempo_seconds,
        active_window_blocks=effective_settings.active_window_blocks,
        active_tempo_blocks=epoch_metadata.active_tempo_blocks,
        epoch_start_block=epoch_metadata.epoch_start_block,
        next_epoch_start_block=epoch_metadata.next_epoch_start_block,
        epoch_started_at=epoch_metadata.epoch_started_at,
        estimated_next_epoch_starts_at=epoch_metadata.estimated_next_epoch_starts_at,
        active_seed_mode=effective_settings.active_seed_mode,
        active_epoch_randomness_source=effective_settings.active_epoch_randomness_source,
        active_epoch_randomness_sha256=(
            active_epoch_randomness_sha256(effective_settings, tempo=active_tempo)
            if include_randomness_hashes
            else None
        ),
        active_selection_seed_sha256=(
            active_selection_seed_sha256(task_registry, effective_settings, tempo=active_tempo)
            if include_randomness_hashes
            else None
        ),
        frontier_depth=effective_settings.frontier_depth,
        validator_capacity=effective_settings.validator_capacity,
        cost_budget_s=effective_settings.curriculum_cost_budget_s,
        base_task_cost_s=effective_settings.curriculum_base_task_cost_s,
        depth_cost_multiplier=effective_settings.curriculum_depth_cost_multiplier,
        cost_limited_K=curriculum_metadata.cost_limited_K,
        estimated_task_cost_s=curriculum_metadata.estimated_task_cost_s,
        active_queue_seed=effective_settings.active_queue_seed,
        task_count=len(tasks),
        tasks=tasks,
    )


def write_current_problems_snapshot(path: Path, snapshot: CurrentProblemsSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = snapshot.model_dump(mode="json", exclude_none=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
