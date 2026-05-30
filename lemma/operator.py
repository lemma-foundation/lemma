"""Operator-facing readiness contracts."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import GenerateJsonSchema

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


class OperatorAlert(BaseModel):
    """Machine-readable service-health alert for dashboards and operator scripts."""

    model_config = ConfigDict(extra="forbid")

    code: str
    level: Literal["critical", "warning", "info"]
    message: str
    active_tempo: int | None = Field(default=None, ge=0)

    @classmethod
    def model_json_schema(
        cls,
        by_alias: bool = True,
        ref_template: str = "#/$defs/{model}",
        schema_generator: type[GenerateJsonSchema] = GenerateJsonSchema,
        mode: Literal["validation", "serialization"] = "validation",
        *,
        union_format: Literal["any_of", "primitive_type_array"] = "any_of",
    ) -> dict[str, object]:
        schema = super().model_json_schema(
            by_alias=by_alias,
            ref_template=ref_template,
            schema_generator=schema_generator,
            mode=mode,
            union_format=union_format,
        )
        if "$defs" not in schema:
            schema["$defs"] = {}
        if "OperatorAlert" not in schema["$defs"]:
            schema["$defs"]["OperatorAlert"] = {
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
                "additionalProperties": schema.get("additionalProperties", False),
            }
        return schema


class OperatorAlertReport(BaseModel):
    """Operator alert pack."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    alert_count: int = Field(ge=0)
    critical_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    alerts: tuple[OperatorAlert, ...]


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
    current_cost_limited_K: int | None = Field(default=None, ge=1)
    current_active_K: int = Field(ge=1)
    can_increase_K: bool
    latest_tempo: int | None = Field(default=None, ge=0)
    latest_active_K: int | None = Field(default=None, ge=1)
    latest_frontier_depth: int | None = Field(default=None, ge=0)
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


def _read_jsonl_records(path: Path) -> tuple[dict[str, object], ...]:
    if not path.exists():
        return ()
    out: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return tuple(out)


def _read_jsonl_records_tail(path: Path, *, limit: int) -> tuple[dict[str, object], ...]:
    records = _read_jsonl_records(path)
    if limit <= 0:
        return ()
    return records[-limit:] if limit < len(records) else records


def _safe_int(value: object, *, default: int = 0) -> int:
    return value if isinstance(value, int) else default


def _safe_float(value: object, *, default: float = 0.0) -> float:
    return value if isinstance(value, float) else default


def _to_utc_datetime(value: str) -> datetime | None:
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError:
        return None


def _is_stale_cache(registry: object, settings: LemmaSettings) -> bool:
    from lemma.tasks import TaskRegistry
    from lemma.validator import active_registry_cache_stale

    if not isinstance(registry, TaskRegistry):
        return True
    return active_registry_cache_stale(registry, settings)


def _load_cached_registry(settings: LemmaSettings, *, tempo: int) -> TaskRegistry | None:
    from lemma.tasks import load_task_registry
    from lemma.validator import active_registry_cache_path

    path = active_registry_cache_path(settings, tempo=tempo)
    if path is None or not path.is_file():
        return None
    return load_task_registry(path.read_bytes())


def _consecutive_failures(records: tuple[dict[str, object], ...], *, field: str = "success") -> int:
    streak = 0
    for payload in reversed(records):
        if bool(payload.get(field)) is True:
            break
        if bool(payload.get(field)) is False:
            streak += 1
            continue
        break
    return streak


def _build_commitment_alerts(
    settings: LemmaSettings,
    now: datetime,
    *,
    recent_failures: int,
    alert_runs: list[OperatorAlert],
) -> None:
    records = _read_jsonl_records_tail(
        settings.operator_data_dir / "commitment-submissions.jsonl",
        limit=max(1, recent_failures),
    )
    if not records:
        return
    streak = _consecutive_failures(records)
    if streak > 0:
        latest = records[-1]
        tempo = (
            _safe_int(latest.get("active_tempo"))
            or _safe_int(latest.get("tempo"))
            or None
        )
        code = "commitment_publish_failures"
        level: Literal["critical", "warning", "info"] = (
            "critical" if streak >= recent_failures else "warning"
        )
        message = f"{streak} consecutive commitment submission failures"
        alert_runs.append(OperatorAlert(code=code, level=level, message=message, active_tempo=tempo))


def _build_weight_alerts(
    settings: LemmaSettings,
    now: datetime,
    *,
    recent_failures: int,
    alert_runs: list[OperatorAlert],
) -> None:
    records = _read_jsonl_records_tail(
        settings.operator_data_dir / "weight-submissions.jsonl",
        limit=max(1, recent_failures),
    )
    if not records:
        return
    streak = _consecutive_failures(records)
    if streak > 0:
        latest = records[-1]
        tempo = (
            _safe_int(latest.get("active_tempo"))
            or _safe_int(latest.get("tempo"))
            or None
        )
        code = "weight_publish_failures"
        level: Literal["critical", "warning", "info"] = (
            "critical" if streak >= recent_failures else "warning"
        )
        message = f"{streak} consecutive set-weights failures"
        alert_runs.append(OperatorAlert(code=code, level=level, message=message, active_tempo=tempo))


def _build_publish_alerts(
    settings: LemmaSettings,
    run_records: tuple[dict[str, object], ...],
    *,
    recent_failures: int,
    alert_runs: list[OperatorAlert],
) -> None:
    path = settings.operator_data_dir / "canonical-publish.jsonl"
    publish_records = _read_jsonl_records_tail(path, limit=max(1, recent_failures))
    latest_tempo = _safe_int(run_records[-1].get("active_tempo"), default=-1) if run_records else None
    latest_tempo = None if latest_tempo is None or latest_tempo < 0 else latest_tempo

    publish_configured = bool(settings.canonical_publish_s3_uri.strip()) or bool(
        settings.canonical_publish_ipfs_api_url.strip()
    )
    if publish_configured and run_records:
        accepted = _safe_int(run_records[-1].get("accepted_unique_count"))
        published = _safe_int(run_records[-1].get("canonical_publish_count"))
        if accepted > 0 and published == 0:
            alert_runs.append(
                OperatorAlert(
                    code="publisher_staging_failure",
                    level="warning",
                    message="latest run accepted proofs but produced no canonical publish rows",
                    active_tempo=latest_tempo,
                )
            )
    if not publish_records:
        return

    failures = 0
    last_error = ""
    for payload in reversed(publish_records):
        kind = payload.get("kind")
        if not isinstance(kind, str) or not kind.endswith("_publish_error"):
            break
        failures += 1
        error = payload.get("error")
        if not last_error and isinstance(error, str):
            last_error = error

    if failures > 0:
        message = f"{failures} consecutive canonical publish staging failures"
        if last_error:
            message += f": {last_error[:120]}"
        alert_runs.append(
            OperatorAlert(
                code="publisher_staging_failure",
                level=(
                    "critical" if failures >= recent_failures else "warning"
                ),
                message=message,
                active_tempo=latest_tempo,
            )
        )


def _build_run_alerts(
    records: tuple[dict[str, object], ...],
    now: datetime,
    *,
    alert_runs: list[OperatorAlert],
) -> None:
    if not records:
        alert_runs.append(
            OperatorAlert(
                code="no_recent_runs",
                level="critical",
                message="no validator-runs.jsonl records found",
            )
        )
        return

    latest = records[-1]
    active_tempo_value: int = _safe_int(latest.get("active_tempo"), default=-1)
    active_tempo: int | None = None if active_tempo_value < 0 else active_tempo_value
    verified = _safe_int(latest.get("verified_count"))
    accepted = _safe_int(latest.get("accepted_unique_count"))
    run_at = latest.get("run_at")
    if run_at is None or not isinstance(run_at, str) or _to_utc_datetime(run_at) is None:
        alert_runs.append(
            OperatorAlert(
                code="invalid_last_run_timestamp",
                level="warning",
                message="latest validator run is missing a parseable run_at timestamp",
                active_tempo=active_tempo,
            )
        )
    else:
        parsed = _to_utc_datetime(run_at)
        if parsed is not None and now - parsed > timedelta(hours=1):
            alert_runs.append(
                OperatorAlert(
                    code="stale_last_run",
                    level="warning",
                    message="latest validator run is older than one hour",
                    active_tempo=active_tempo,
                )
            )

    if verified == 0:
        alert_runs.append(
            OperatorAlert(
                code="zero_reveals",
                level="warning",
                message="latest validator run consumed zero verification records",
                active_tempo=active_tempo,
            )
        )
    if accepted == 0:
        alert_runs.append(
            OperatorAlert(
                code="zero_accepted",
                level="warning",
                message="latest validator run accepted zero unique proofs",
                active_tempo=active_tempo,
            )
        )

    if len(records) >= 3:
        streak_accepted = 0
        streak_verified = 0
        for payload in reversed(records[-3:]):
            if _safe_int(payload.get("accepted_unique_count")) > 0:
                break
            streak_accepted += 1
        for payload in reversed(records[-3:]):
            if _safe_int(payload.get("verified_count")) > 0:
                break
            streak_verified += 1
        if streak_accepted >= 3:
            alert_runs.append(
                OperatorAlert(
                    code="service_restart_loop",
                    level="critical",
                    message="three consecutive runs had zero accepted unique proofs",
                    active_tempo=active_tempo,
                )
            )
        if streak_verified >= 3:
            alert_runs.append(
                OperatorAlert(
                    code="service_restart_loop",
                    level="critical",
                    message="three consecutive runs had zero consumed verification records",
                    active_tempo=active_tempo,
                )
            )


def build_operator_alerts(
    settings: LemmaSettings,
    *,
    now: datetime | None = None,
    recent_runs: int = 5,
    recent_failures: int = 3,
) -> OperatorAlertReport:
    """Build operator alert summary from recent local logs."""
    now = now or datetime.now(UTC)
    alerts: list[OperatorAlert] = []
    run_records = _read_jsonl_records_tail(settings.operator_data_dir / "validator-runs.jsonl", limit=recent_runs)
    _build_run_alerts(run_records, now, alert_runs=alerts)
    _build_commitment_alerts(
        settings,
        now,
        recent_failures=max(1, recent_failures),
        alert_runs=alerts,
    )
    _build_weight_alerts(
        settings,
        now,
        recent_failures=max(1, recent_failures),
        alert_runs=alerts,
    )
    _build_publish_alerts(
        settings,
        run_records,
        recent_failures=max(1, recent_failures),
        alert_runs=alerts,
    )

    latest_run_tempo = None
    if run_records:
        latest_tempo = run_records[-1].get("active_tempo")
        latest_run_tempo = latest_tempo if isinstance(latest_tempo, int) and latest_tempo >= 0 else None
    if latest_run_tempo is not None:
        try:
            registry = _load_cached_registry(settings, tempo=latest_run_tempo)
            if registry is None:
                if settings.active_registry_role == "auditor":
                    alerts.append(
                        OperatorAlert(
                            code="cache_divergence",
                            level="critical",
                            message=f"active-registry cache missing for tempo {latest_run_tempo}",
                            active_tempo=latest_run_tempo,
                        )
                    )
            elif _is_stale_cache(registry, settings):
                alerts.append(
                    OperatorAlert(
                        code="cache_divergence",
                        level="warning",
                        message=f"active-registry cache appears stale for tempo {latest_run_tempo}",
                        active_tempo=latest_run_tempo,
                    )
                )
        except Exception as exc:  # pragma: no cover - robust fallback
            alerts.append(
                OperatorAlert(
                    code="cache_divergence",
                    level="warning",
                    message=f"active-registry cache check failed: {exc}",
                    active_tempo=latest_run_tempo,
                )
            )

    critical_count = sum(1 for alert in alerts if alert.level == "critical")
    warning_count = sum(1 for alert in alerts if alert.level == "warning")
    return OperatorAlertReport(
        schema_version=1,
        alert_count=len(alerts),
        critical_count=critical_count,
        warning_count=warning_count,
        alerts=tuple(alerts),
    )



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
    from lemma.supply.controller import CurriculumConfig, cost_limited_k, target_active_k

    config = CurriculumConfig(
        beta=settings.curriculum_beta,
        low_band=settings.curriculum_low_band,
        high_band=settings.curriculum_high_band,
        k_min=settings.curriculum_k_min,
        k_max=settings.curriculum_k_max,
        cost_budget_s=settings.curriculum_cost_budget_s,
        base_task_cost_s=settings.curriculum_base_task_cost_s,
        depth_cost_multiplier=settings.curriculum_depth_cost_multiplier,
    )
    current_cost_cap = cost_limited_k(current_frontier_depth, config)
    validator_capacity = settings.validator_capacity or current_active_K
    target_k = target_active_k(validator_capacity, frontier_depth=current_frontier_depth, config=config)
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
        current_cost_limited_K=current_cost_cap,
        current_active_K=current_active_K,
        can_increase_K=(
            settings.curriculum_retarget_enabled
            and target_k > current_active_K
        ),
        latest_tempo=latest.tempo if latest is not None else None,
        latest_active_K=latest.active_K if latest is not None else None,
        latest_frontier_depth=latest.frontier_depth if latest is not None else None,
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
    depth_counts = _summary_counts(summary.get("queue_depth_counts"))
    band_counts = _summary_counts(summary.get("difficulty_band_counts"))
    coverage = summary["metadata_coverage"]
    dependency_coverage = _summary_int(coverage.get("dependency_depth")) if isinstance(coverage, dict) else 0
    return _check(
        "source_snapshot",
        rows > 0 and max_depth >= frontier_depth,
        (
            f"rows={rows} max_depth={max_depth} frontier_rows={frontier_rows} "
            f"depths={_format_counts(depth_counts)} bands={_format_counts(band_counts)} "
            f"dependency_coverage={dependency_coverage}/{rows}"
        ),
    )


def _summary_int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _summary_counts(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(item, int) and not isinstance(item, bool)}


def _format_counts(counts: dict[str, int]) -> str:
    return ",".join(f"{key}:{counts[key]}" for key in sorted(counts)) or "none"


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
