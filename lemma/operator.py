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
    from lemma.tasks import TaskError, fetch_task_registry
    from lemma.validator import active_tasks_for_validation

    checks: list[OperatorPreflightCheck] = []
    active_task_ids: tuple[str, ...] = ()
    registry_inspect: OperatorRegistryInspectReport | None = None
    registry = None

    try:
        registry = fetch_task_registry(
            settings,
            verify_signature=settings.protocol_mode == "production" or settings.verify_registry_signatures,
        )
        checks.append(_check("registry_load", True, f"{len(registry.tasks)} tasks"))
    except (TaskError, OSError) as e:
        checks.append(_check("registry_load", False, str(e)))

    expected_pin = (settings.task_registry_sha256_expected or "").strip()
    checks.append(
        _check(
            "registry_hash_pin",
            bool(expected_pin),
            "LEMMA_TASK_REGISTRY_SHA256_EXPECTED is set" if expected_pin else "missing registry SHA256 pin",
        )
    )

    if registry is not None:
        signature_ok = registry.signature_status == "verified" if settings.protocol_mode == "production" else True
        checks.append(_check("registry_signature", signature_ok, registry.signature_status))
        try:
            active_tasks = active_tasks_for_validation(registry, settings)
        except RuntimeError as e:
            checks.append(_check("active_window", False, str(e)))
        else:
            active_task_ids = tuple(task.id for task in active_tasks)
            registry_inspect = _inspect_registry(registry, settings, active_task_count=len(active_tasks))
            checks.append(
                _check(
                    "active_window",
                    len(active_tasks) == settings.active_task_count,
                    (
                        f"{len(active_tasks)} active / K={settings.active_task_count} "
                        f"at frontier_depth={settings.frontier_depth}"
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

        rejections = production_supply_rejections(registry) if registry is not None else ()
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
                    "procedural_supply",
                    not rejections,
                    (
                        "paid supply is procedural depth-2"
                        if not rejections
                        else "paid supply rejected: " + ", ".join(rejections[:5])
                    ),
                ),
            ]
        )

    preflight = OperatorPreflightReport(
        schema_version=1,
        ok=all(check.ok for check in checks),
        registry_sha256=registry.sha256 if registry is not None else None,
        active_K=settings.active_task_count,
        frontier_depth=settings.frontier_depth,
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
        artifacts=_summarize_artifacts(settings),
    )


def build_operator_registry_inspect(settings: LemmaSettings) -> OperatorRegistryInspectReport:
    """Summarize registry supply depth using the validator's active-window logic."""
    from lemma.tasks import fetch_task_registry
    from lemma.validator import active_tasks_for_validation

    registry = fetch_task_registry(settings)
    active_tasks = active_tasks_for_validation(registry, settings)
    return _inspect_registry(registry, settings, active_task_count=len(active_tasks))
