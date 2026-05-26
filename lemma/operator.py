"""Operator-facing readiness contracts."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lemma.common.config import LemmaSettings

if TYPE_CHECKING:
    from lemma.tasks import TaskRegistry

PreflightCheckName = Literal[
    "registry_load",
    "registry_hash_pin",
    "registry_signature",
    "active_window",
    "corpus_output_dir",
    "operator_data_dir",
    "submission_spool_dir",
    "lean_verifier",
    "production_domains",
    "lean_network",
    "live_submission_signatures",
    "commit_reveal",
    "strong_proof_identity",
    "epoch_randomness",
    "curriculum_controller",
    "source_snapshot",
    "import_graph",
    "procedural_supply",
]


class OperatorPreflightCheck(BaseModel):
    """One machine-readable operator readiness check."""

    model_config = ConfigDict(extra="forbid")

    name: PreflightCheckName
    ok: bool
    detail: str


class OperatorPreflightReport(BaseModel):
    """Stable JSON contract emitted by `lemma operator preflight`."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    ok: bool
    registry_sha256: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    active_K: int = Field(ge=1)
    frontier_depth: int = Field(ge=0)
    checks: tuple[OperatorPreflightCheck, ...]

    @model_validator(mode="after")
    def _ok_matches_checks(self) -> OperatorPreflightReport:
        if self.ok != all(check.ok for check in self.checks):
            raise ValueError("ok must equal all(check.ok)")
        return self


class OperatorRegistryInspectReport(BaseModel):
    """Compact public registry supply summary."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    registry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    total_task_count: int = Field(ge=0)
    active_K: int = Field(ge=1)
    frontier_depth: int = Field(ge=0)
    active_task_count: int = Field(ge=0)
    eligible_task_count: int = Field(ge=0)
    waiting_task_count: int = Field(ge=0)
    parked_task_count: int = Field(ge=0)
    max_queue_depth: int = Field(ge=0)
    queue_depth_counts: dict[str, int]


class OperatorArtifactSummary(BaseModel):
    """Counts of local replay/support artifacts without paths or contents."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    validator_run_count: int = Field(ge=0)
    verification_record_count: int = Field(ge=0)
    score_event_count: int = Field(ge=0)
    corpus_jsonl_file_count: int = Field(ge=0)
    corpus_row_count: int = Field(ge=0)


class OperatorCurriculumSummary(BaseModel):
    """Public-safe curriculum controller state."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    enabled: bool
    state_public: bool
    validator_capacity: int = Field(ge=0)
    k_min: int = Field(ge=1)
    k_max: int = Field(ge=1)
    cost_budget_s: float = Field(ge=0.0)
    base_task_cost_s: float = Field(ge=0.0)
    depth_cost_multiplier: float = Field(ge=1.0)
    window_base_blocks: int = Field(ge=1)
    window_max_blocks: int = Field(ge=1)
    window_depth_multiplier: float = Field(ge=1.0)
    window_k_reference: int = Field(ge=1)
    current_cost_limited_K: int | None = Field(default=None, ge=1)
    current_active_K: int = Field(ge=1)
    current_active_window_blocks: int = Field(ge=1)
    can_increase_K: bool
    latest_tempo: int | None = Field(default=None, ge=0)
    latest_active_K: int | None = Field(default=None, ge=1)
    latest_frontier_depth: int | None = Field(default=None, ge=0)
    latest_active_window_blocks: int | None = Field(default=None, ge=1)
    latest_ema_solve_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    latest_solved_slots: int | None = Field(default=None, ge=0)
    latest_action: str | None = None
    latest_variant_stream_requested: bool | None = None


class OperatorDiagnosticsReport(BaseModel):
    """Public-safe local support report for operator debugging."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    preflight: OperatorPreflightReport
    registry_sha256: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    active_K: int = Field(ge=1)
    frontier_depth: int = Field(ge=0)
    active_task_ids: tuple[str, ...]
    registry_inspect: OperatorRegistryInspectReport | None
    curriculum: OperatorCurriculumSummary
    artifacts: OperatorArtifactSummary


def _check(name: PreflightCheckName, ok: bool, detail: str) -> OperatorPreflightCheck:
    return OperatorPreflightCheck(name=name, ok=ok, detail=detail)


def _ensure_dir(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"unavailable: {e.strerror or e.__class__.__name__}"
    return True, "ready"


def _count_jsonl_rows(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _summarize_artifacts(settings: LemmaSettings) -> OperatorArtifactSummary:
    corpus_files = sorted(settings.corpus_output_dir.glob("*.jsonl"))
    return OperatorArtifactSummary(
        schema_version=1,
        validator_run_count=_count_jsonl_rows(settings.operator_data_dir / "validator-runs.jsonl"),
        verification_record_count=_count_jsonl_rows(settings.operator_data_dir / "verification-records.jsonl"),
        score_event_count=_count_jsonl_rows(settings.operator_data_dir / "score-events.jsonl"),
        corpus_jsonl_file_count=len(corpus_files),
        corpus_row_count=sum(_count_jsonl_rows(path) for path in corpus_files),
    )


def _summarize_curriculum(
    settings: LemmaSettings,
    *,
    current_active_K: int,
    current_frontier_depth: int,
) -> OperatorCurriculumSummary:
    latest = None
    if settings.curriculum_retarget_enabled and settings.curriculum_state_jsonl is not None:
        from lemma.supply.controller import read_curriculum_records

        records = read_curriculum_records(settings.curriculum_state_jsonl)
        latest = records[-1] if records else None
    from lemma.supply.controller import CurriculumConfig, cost_limited_k, target_active_k, target_window_blocks

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
    current_cost_cap = cost_limited_k(current_frontier_depth, config)
    validator_capacity = settings.validator_capacity or current_active_K
    target_k = target_active_k(validator_capacity, frontier_depth=current_frontier_depth, config=config)
    current_window_blocks = target_window_blocks(
        frontier_depth=current_frontier_depth,
        active_k=current_active_K,
        config=config,
    )
    return OperatorCurriculumSummary(
        schema_version=1,
        enabled=settings.curriculum_retarget_enabled,
        state_public=settings.curriculum_state_public,
        validator_capacity=settings.validator_capacity,
        k_min=settings.curriculum_k_min,
        k_max=settings.curriculum_k_max,
        cost_budget_s=settings.curriculum_cost_budget_s,
        base_task_cost_s=settings.curriculum_base_task_cost_s,
        depth_cost_multiplier=settings.curriculum_depth_cost_multiplier,
        window_base_blocks=settings.curriculum_window_base_blocks,
        window_max_blocks=settings.curriculum_window_max_blocks,
        window_depth_multiplier=settings.curriculum_window_depth_multiplier,
        window_k_reference=settings.curriculum_window_k_reference,
        current_cost_limited_K=current_cost_cap,
        current_active_K=current_active_K,
        current_active_window_blocks=current_window_blocks,
        can_increase_K=(
            settings.curriculum_retarget_enabled
            and target_k > current_active_K
        ),
        latest_tempo=latest.tempo if latest is not None else None,
        latest_active_K=latest.active_K if latest is not None else None,
        latest_frontier_depth=latest.frontier_depth if latest is not None else None,
        latest_active_window_blocks=latest.active_window_blocks if latest is not None else None,
        latest_ema_solve_rate=latest.ema_solve_rate if latest is not None else None,
        latest_solved_slots=latest.solved_slots if latest is not None else None,
        latest_action=latest.action if latest is not None else None,
        latest_variant_stream_requested=latest.variant_stream_requested if latest is not None else None,
    )


def _source_snapshot_check(settings: LemmaSettings, *, frontier_depth: int) -> OperatorPreflightCheck:
    if settings.procedural_source_jsonl is None:
        return _check("source_snapshot", False, "missing LEMMA_PROCEDURAL_SOURCE_JSONL")
    try:
        from lemma.supply.mathlib_snapshot import rows_from_jsonl, snapshot_quality_summary

        summary = snapshot_quality_summary(rows_from_jsonl(settings.procedural_source_jsonl))
    except (OSError, ValueError) as e:
        return _check("source_snapshot", False, f"invalid source snapshot: {e.__class__.__name__}")
    rows = _summary_int(summary.get("rows"))
    max_depth = _summary_int(summary.get("max_queue_depth"))
    frontier_rows = _summary_int(summary.get("frontier_rows"))
    coverage = summary["metadata_coverage"]
    dependency_coverage = _summary_int(coverage.get("dependency_depth")) if isinstance(coverage, dict) else 0
    return _check(
        "source_snapshot",
        rows > 0 and max_depth >= frontier_depth,
        (
            f"rows={rows} max_depth={max_depth} frontier_rows={frontier_rows} "
            f"dependency_coverage={dependency_coverage}/{rows}"
        ),
    )


def _summary_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _inspect_registry(
    registry: TaskRegistry,
    settings: LemmaSettings,
    *,
    active_task_count: int,
) -> OperatorRegistryInspectReport:
    eligible_count = sum(1 for task in registry.tasks if task.queue_depth <= settings.frontier_depth)
    parked_count = len(registry.tasks) - eligible_count
    queue_depth_counts = Counter(str(task.queue_depth) for task in registry.tasks)
    return OperatorRegistryInspectReport(
        schema_version=1,
        registry_sha256=registry.sha256,
        total_task_count=len(registry.tasks),
        active_K=settings.active_task_count,
        frontier_depth=settings.frontier_depth,
        active_task_count=active_task_count,
        eligible_task_count=eligible_count,
        waiting_task_count=max(0, eligible_count - active_task_count),
        parked_task_count=parked_count,
        max_queue_depth=max((task.queue_depth for task in registry.tasks), default=0),
        queue_depth_counts=dict(sorted(queue_depth_counts.items(), key=lambda item: int(item[0]))),
    )


def _build_operator_state(
    settings: LemmaSettings,
) -> tuple[OperatorPreflightReport, tuple[str, ...], OperatorRegistryInspectReport | None]:
    from lemma.tasks import TaskError
    from lemma.validator import (
        active_tasks_for_validation,
        current_active_tempo,
        curriculum_controlled_settings,
        task_registry_for_validation,
    )

    checks: list[OperatorPreflightCheck] = []
    active_task_ids: tuple[str, ...] = ()
    registry_inspect: OperatorRegistryInspectReport | None = None
    registry = None
    active_window_settings = settings

    try:
        active_tempo = current_active_tempo(settings)
        active_window_settings = curriculum_controlled_settings(settings, tempo=active_tempo)
        registry = task_registry_for_validation(active_window_settings, tempo=active_tempo)
        checks.append(_check("registry_load", True, f"{len(registry.tasks)} tasks"))
    except (RuntimeError, TaskError, OSError) as e:
        checks.append(_check("registry_load", False, str(e)))

    production_or_procedural = settings.protocol_mode == "production" or settings.task_supply_mode == "procedural"
    expected_pin = (
        (settings.procedural_source_sha256_expected or "").strip()
        if production_or_procedural
        else (settings.task_registry_sha256_expected or "").strip()
    )
    checks.append(
        _check(
            "registry_hash_pin",
            bool(expected_pin),
            (
                "procedural source SHA256 pin is set"
                if production_or_procedural and expected_pin
                else "LEMMA_TASK_REGISTRY_SHA256_EXPECTED is set"
                if expected_pin
                else "missing procedural source SHA256 pin"
                if production_or_procedural
                else "missing registry SHA256 pin"
            ),
        )
    )
    if production_or_procedural:
        checks.append(_source_snapshot_check(settings, frontier_depth=active_window_settings.frontier_depth))

    if registry is not None:
        signature_ok = registry.signature_status == "verified" if settings.protocol_mode == "production" else True
        if settings.task_supply_mode == "procedural":
            signature_ok = True
        checks.append(_check("registry_signature", signature_ok, registry.signature_status))
        try:
            active_tasks = active_tasks_for_validation(registry, active_window_settings, tempo=active_tempo)
        except RuntimeError as e:
            checks.append(_check("active_window", False, str(e)))
        else:
            active_task_ids = tuple(task.id for task in active_tasks)
            registry_inspect = _inspect_registry(
                registry,
                active_window_settings,
                active_task_count=len(active_tasks),
            )
            checks.append(
                _check(
                    "active_window",
                    len(active_tasks) == active_window_settings.active_task_count,
                    (
                        f"{len(active_tasks)} active / K={active_window_settings.active_task_count} "
                        f"at frontier_depth={active_window_settings.frontier_depth}"
                    ),
                )
            )

    corpus_ok, corpus_detail = _ensure_dir(settings.corpus_output_dir)
    checks.append(_check("corpus_output_dir", corpus_ok, corpus_detail))
    operator_ok, operator_detail = _ensure_dir(settings.operator_data_dir)
    checks.append(_check("operator_data_dir", operator_ok, operator_detail))
    if settings.submission_spool_dir is not None:
        spool_ok, spool_detail = _ensure_dir(settings.submission_spool_dir)
        checks.append(_check("submission_spool_dir", spool_ok, spool_detail))

    curriculum_summary = _summarize_curriculum(
        settings,
        current_active_K=active_window_settings.active_task_count,
        current_frontier_depth=active_window_settings.frontier_depth,
    )
    checks.append(
        _check(
            "curriculum_controller",
            settings.curriculum_k_max >= settings.curriculum_k_min,
            (
                "retarget disabled"
                if not curriculum_summary.enabled
                else (
                    f"retarget enabled capacity={curriculum_summary.validator_capacity} "
                    f"state_public={str(curriculum_summary.state_public).lower()} "
                    f"k_range={curriculum_summary.k_min}-{curriculum_summary.k_max} "
                    f"current_K={curriculum_summary.current_active_K} "
                    f"cost_cap={curriculum_summary.current_cost_limited_K or 'off'} "
                    f"can_increase_K={str(curriculum_summary.can_increase_K).lower()}"
                )
            ),
        )
    )

    if (settings.lean_verify_remote_url or "").strip():
        verifier_detail = "remote Lean worker configured"
        verifier_ok = True
    elif settings.lean_use_docker:
        verifier_detail = f"Docker verifier image {settings.lean_sandbox_image}"
        verifier_ok = bool(settings.lean_sandbox_image.strip())
    else:
        verifier_detail = "host Lean enabled" if settings.allow_host_lean else "no Lean verifier backend configured"
        verifier_ok = settings.allow_host_lean
    checks.append(_check("lean_verifier", verifier_ok, verifier_detail))

    if settings.protocol_mode == "production":
        from lemma.protocol_invariants import production_supply_rejections
        from lemma.supply.import_graph import read_import_graph

        import_graph = (
            read_import_graph(settings.procedural_import_graph_jsonl)
            if settings.procedural_import_graph_jsonl
            else None
        )

        rejections = production_supply_rejections(registry, import_graph=import_graph) if registry is not None else ()
        procedural_ok = settings.task_supply_mode == "procedural" and not rejections
        checks.extend(
            [
                _check(
                    "production_domains",
                    tuple(settings.enabled_domains) == ("lean",),
                    "enabled domains must be exactly lean",
                ),
                _check(
                    "lean_network",
                    settings.lean_sandbox_network.strip().lower() in {"none", "no"},
                    "production Lean verifier network must be disabled",
                ),
                _check(
                    "live_submission_signatures",
                    settings.require_submission_signatures,
                    "live miner authentication must be enabled",
                ),
                _check(
                    "commit_reveal",
                    settings.require_commit_reveal,
                    "LEMMA_REQUIRE_COMMIT_REVEAL must be enabled",
                ),
                _check(
                    "strong_proof_identity",
                    settings.require_strong_proof_identity,
                    "LEMMA_REQUIRE_STRONG_PROOF_IDENTITY must be enabled",
                ),
                _check(
                    "epoch_randomness",
                    settings.active_seed_mode == "epoch_randomness"
                    and settings.active_epoch_randomness_source == "chain_drand",
                    "production active selection must use chain/drand epoch randomness",
                ),
                _check(
                    "import_graph",
                    import_graph is not None and import_graph.entry_count > 0,
                    "LEMMA_PROCEDURAL_IMPORT_GRAPH_JSONL must point to a public import graph",
                ),
                _check(
                    "procedural_supply",
                    procedural_ok,
                    (
                        "paid supply is procedural depth-2"
                        if procedural_ok
                        else "LEMMA_TASK_SUPPLY_MODE must be procedural"
                        if settings.task_supply_mode != "procedural"
                        else "paid supply rejected: " + ", ".join(rejections[:5])
                    ),
                ),
            ]
        )

    preflight = OperatorPreflightReport(
        schema_version=1,
        ok=all(check.ok for check in checks),
        registry_sha256=registry.sha256 if registry is not None else None,
        active_K=active_window_settings.active_task_count,
        frontier_depth=active_window_settings.frontier_depth,
        checks=tuple(checks),
    )
    return preflight, active_task_ids, registry_inspect


def build_operator_preflight(settings: LemmaSettings) -> OperatorPreflightReport:
    """Build the readiness report without running a scoring pass."""
    return _build_operator_state(settings)[0]


def build_operator_diagnostics(settings: LemmaSettings) -> OperatorDiagnosticsReport:
    """Build a public-safe diagnostics report for support and replay."""
    preflight, active_task_ids, registry_inspect = _build_operator_state(settings)
    return OperatorDiagnosticsReport(
        schema_version=1,
        preflight=preflight,
        registry_sha256=preflight.registry_sha256,
        active_K=preflight.active_K,
        frontier_depth=preflight.frontier_depth,
        active_task_ids=active_task_ids,
        registry_inspect=registry_inspect,
        curriculum=_summarize_curriculum(
            settings,
            current_active_K=preflight.active_K,
            current_frontier_depth=preflight.frontier_depth,
        ),
        artifacts=_summarize_artifacts(settings),
    )


def build_operator_registry_inspect(settings: LemmaSettings) -> OperatorRegistryInspectReport:
    """Summarize registry supply depth using the validator's active-window logic."""
    from lemma.validator import (
        active_tasks_for_validation,
        current_active_tempo,
        curriculum_controlled_settings,
        task_registry_for_validation,
    )

    active_tempo = current_active_tempo(settings)
    active_window_settings = curriculum_controlled_settings(settings, tempo=active_tempo)
    registry = task_registry_for_validation(active_window_settings, tempo=active_tempo)
    active_tasks = active_tasks_for_validation(registry, active_window_settings, tempo=active_tempo)
    return _inspect_registry(registry, active_window_settings, active_task_count=len(active_tasks))
