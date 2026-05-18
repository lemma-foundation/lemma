"""Operator-facing readiness contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lemma.common.config import LemmaSettings

PreflightCheckName = Literal[
    "registry_load",
    "registry_hash_pin",
    "registry_signature",
    "active_window",
    "corpus_output_dir",
    "operator_data_dir",
    "lean_verifier",
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


class OperatorDiagnosticsReport(BaseModel):
    """Public-safe local support report for operator debugging."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    preflight: OperatorPreflightReport
    registry_sha256: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    active_K: int = Field(ge=1)
    frontier_depth: int = Field(ge=0)
    active_task_ids: tuple[str, ...]


def _check(name: PreflightCheckName, ok: bool, detail: str) -> OperatorPreflightCheck:
    return OperatorPreflightCheck(name=name, ok=ok, detail=detail)


def _ensure_dir(path: Path) -> tuple[bool, str]:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"unavailable: {e.strerror or e.__class__.__name__}"
    return True, "ready"


def _build_operator_state(settings: LemmaSettings) -> tuple[OperatorPreflightReport, tuple[str, ...]]:
    from lemma.tasks import TaskError, fetch_task_registry
    from lemma.validator import active_tasks_for_validation

    checks: list[OperatorPreflightCheck] = []
    active_task_ids: tuple[str, ...] = ()
    registry = None

    try:
        registry = fetch_task_registry(settings)
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
        checks.append(_check("registry_signature", True, registry.signature_status))
        active_tasks = active_tasks_for_validation(registry, settings)
        active_task_ids = tuple(task.id for task in active_tasks)
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

    preflight = OperatorPreflightReport(
        schema_version=1,
        ok=all(check.ok for check in checks),
        registry_sha256=registry.sha256 if registry is not None else None,
        active_K=settings.active_task_count,
        frontier_depth=settings.frontier_depth,
        checks=tuple(checks),
    )
    return preflight, active_task_ids


def build_operator_preflight(settings: LemmaSettings) -> OperatorPreflightReport:
    """Build the readiness report without running a scoring pass."""
    return _build_operator_state(settings)[0]


def build_operator_diagnostics(settings: LemmaSettings) -> OperatorDiagnosticsReport:
    """Build a public-safe diagnostics report for support and replay."""
    preflight, active_task_ids = _build_operator_state(settings)
    return OperatorDiagnosticsReport(
        schema_version=1,
        preflight=preflight,
        registry_sha256=preflight.registry_sha256,
        active_K=preflight.active_K,
        frontier_depth=preflight.frontier_depth,
        active_task_ids=active_task_ids,
    )
