"""SorryDB source ingester.

Turn a batch of public SorryDB rows into packaged patch-task candidates without
ever crashing on a bad row. Each row is run through the source filter and either
accepted (with optional baseline screening) or quarantined with a structured
reason. The run emits a deterministic :class:`IngestReport` plus the accepted
:class:`LemmaTask` list, so two operators reproduce the same task set from public
inputs alone.

This is pure data tooling: no model calls, no proof generation.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lemma.ingest.baseline import BaselineProber
from lemma.ingest.report import (
    EnvironmentCertification,
    IngestCandidate,
    IngestReport,
    IngestResult,
    QuarantinedRow,
    QuarantineReason,
)
from lemma.source_sorries import (
    build_patch_task_from_sorrydb_record,
    source_environment_sha256,
    source_ref_from_sorrydb_record,
)
from lemma.tasks import LemmaTask

#: Resolve the local pinned checkout root for a row's repo@commit, or None.
SourceRootResolver = Callable[[Mapping[str, Any]], Path | None]

_PINNED_TOOLCHAIN_RE = re.compile(r"^leanprover/lean4:v\d+\.\d+\.\d+(?:-rc\d+)?$")
_LEAN3_RE = re.compile(r"(^|[^0-9])3\.\d+")

#: Map known build-time ValueErrors to a structured quarantine reason.
_ERROR_REASONS: tuple[tuple[str, QuarantineReason], ...] = (
    ("does not exist in checkout", "source_file_missing"),
    ("no sorry/admit hole", "no_target_hole"),
    ("extra sorry/admit holes", "extra_holes"),
    ("target declaration not found", "decl_not_found"),
    ("unsafe source path", "unsafe_path"),
    ("missing string field", "malformed_row"),
    ("missing object field", "malformed_row"),
)


def ingest_sorrydb_rows(
    rows: Sequence[Any],
    *,
    resolve_source_root: SourceRootResolver,
    source_license: str,
    mathlib_rev: str,
    default_theorem_name: str | None = None,
    default_type_expr: str | None = None,
    lean_toolchain: str | None = None,
    reproduction_command: str = "lake build",
    allow_extra_holes: bool = False,
    allow_unstable_toolchain: bool = False,
    baseline_prober: BaselineProber | None = None,
) -> IngestResult:
    """Package a batch of SorryDB rows into task candidates + a report."""
    report = IngestReport(source_stream="sorrydb", total_rows=len(rows))
    result = IngestResult(report=report)
    seen_targets: set[str] = set()
    env_seen: set[tuple[str, str]] = set()

    for index, row in enumerate(rows):
        outcome = _ingest_row(
            row,
            resolve_source_root=resolve_source_root,
            source_license=source_license,
            mathlib_rev=mathlib_rev,
            default_theorem_name=default_theorem_name,
            default_type_expr=default_type_expr,
            lean_toolchain=lean_toolchain,
            reproduction_command=reproduction_command,
            allow_extra_holes=allow_extra_holes,
            allow_unstable_toolchain=allow_unstable_toolchain,
            baseline_prober=baseline_prober,
            seen_targets=seen_targets,
            queue_position=index,
        )
        for env in outcome.environments:
            key = (env.repo, env.commit)
            if key not in env_seen:
                env_seen.add(key)
                report.environments.append(env)
        if outcome.quarantine is not None:
            report.quarantined.append(outcome.quarantine)
            continue
        assert outcome.task is not None and outcome.candidate is not None
        seen_targets.add(outcome.task.target_sha256)
        result.tasks.append(outcome.task)
        report.accepted.append(outcome.candidate)

    return result


@dataclass
class _RowOutcome:
    task: LemmaTask | None = None
    candidate: IngestCandidate | None = None
    quarantine: QuarantinedRow | None = None
    environments: list[EnvironmentCertification] = field(default_factory=list)


def _ingest_row(
    row: Any,
    *,
    resolve_source_root: SourceRootResolver,
    source_license: str,
    mathlib_rev: str,
    default_theorem_name: str | None,
    default_type_expr: str | None,
    lean_toolchain: str | None,
    reproduction_command: str,
    allow_extra_holes: bool,
    allow_unstable_toolchain: bool,
    baseline_prober: BaselineProber | None,
    seen_targets: set[str],
    queue_position: int,
) -> _RowOutcome:
    row_ref = _row_ref(row)

    if not isinstance(row, Mapping) or not isinstance(row.get("repo"), Mapping):
        return _RowOutcome(quarantine=QuarantinedRow(row_ref=row_ref, reason="malformed_row"))

    if not source_license.strip():
        return _RowOutcome(quarantine=QuarantinedRow(row_ref=row_ref, reason="missing_license"))

    theorem_name = _row_string(row, "theorem_name", default_theorem_name)
    if theorem_name is None:
        return _RowOutcome(quarantine=QuarantinedRow(row_ref=row_ref, reason="missing_theorem_name"))
    type_expr = _row_string(row, "type_expr", default_type_expr)
    if type_expr is None:
        return _RowOutcome(quarantine=QuarantinedRow(row_ref=row_ref, reason="missing_type_expr"))

    try:
        source_ref = source_ref_from_sorrydb_record(row)
    except ValueError as e:
        return _RowOutcome(quarantine=QuarantinedRow(row_ref=row_ref, reason=_classify_error(str(e)), detail=str(e)))

    repo = str(source_ref.url or "")
    commit = str(source_ref.commit or "")

    lean_version = str(row["repo"].get("lean_version") or "").strip()
    if _is_lean3(lean_version):
        return _RowOutcome(quarantine=QuarantinedRow(row_ref=row_ref, reason="not_lean4", detail=lean_version))
    resolved_toolchain = _resolve_toolchain(lean_toolchain, lean_version)
    if not allow_unstable_toolchain and not _is_pinned_toolchain(resolved_toolchain):
        return _RowOutcome(
            quarantine=QuarantinedRow(row_ref=row_ref, reason="unstable_toolchain", detail=resolved_toolchain)
        )

    source_root = resolve_source_root(row)
    if source_root is None or not source_root.is_dir():
        return _RowOutcome(quarantine=QuarantinedRow(row_ref=row_ref, reason="checkout_missing"))

    environment_sha256 = source_environment_sha256(source_root)
    env_cert = _certify_environment(
        repo=repo,
        commit=commit,
        source_root=source_root,
        toolchain=resolved_toolchain,
        environment_sha256=environment_sha256,
    )
    environments = [env_cert]

    if environment_sha256 is None:
        return _RowOutcome(
            quarantine=QuarantinedRow(row_ref=row_ref, reason="missing_environment", detail="no_environment_hash"),
            environments=environments,
        )
    if not env_cert.lake_manifest_present:
        return _RowOutcome(
            quarantine=QuarantinedRow(row_ref=row_ref, reason="missing_environment", detail="no_lake_manifest"),
            environments=environments,
        )

    try:
        task = build_patch_task_from_sorrydb_record(
            row,
            source_root=source_root,
            theorem_name=theorem_name,
            type_expr=type_expr,
            source_license=source_license,
            mathlib_rev=mathlib_rev,
            task_id=_row_string(row, "task_id", None),
            lean_toolchain=resolved_toolchain,
            reproduction_command=reproduction_command,
            allow_extra_holes=allow_extra_holes,
        )
    except ValueError as e:
        return _RowOutcome(
            quarantine=QuarantinedRow(row_ref=row_ref, reason=_classify_error(str(e)), detail=str(e)),
            environments=environments,
        )

    if task.target_sha256 in seen_targets:
        return _RowOutcome(
            quarantine=QuarantinedRow(row_ref=row_ref, reason="duplicate_target", detail=task.target_sha256),
            environments=environments,
        )

    task = task.model_copy(update={"queue_position": queue_position})

    baseline_status = "unscreened"
    baseline_tactic = ""
    if baseline_prober is not None:
        probe = baseline_prober(task, source_root)
        if probe.trivial:
            return _RowOutcome(
                quarantine=QuarantinedRow(row_ref=row_ref, reason="baseline_trivial", detail=probe.tactic),
                environments=environments,
            )
        baseline_status = "nontrivial"

    candidate = IngestCandidate(
        task_id=task.id,
        row_ref=row_ref,
        repo=repo,
        commit=commit,
        path=str(source_ref.path or ""),
        theorem_name=task.theorem_name,
        target_sha256=task.target_sha256,
        target_type_sha256=task.target_type_sha256,
        environment_sha256=task.environment_sha256,
        lean_toolchain=task.lean_toolchain,
        baseline_status=baseline_status,  # type: ignore[arg-type]
        baseline_tactic=baseline_tactic,
    )
    return _RowOutcome(task=task, candidate=candidate, environments=environments)


def _certify_environment(
    *,
    repo: str,
    commit: str,
    source_root: Path,
    toolchain: str,
    environment_sha256: str | None,
) -> EnvironmentCertification:
    manifest_present = (source_root / "lake-manifest.json").is_file()
    pinned = _is_pinned_toolchain(toolchain)
    reasons: list[str] = []
    if environment_sha256 is None:
        reasons.append("no_environment_hash")
    if not manifest_present:
        reasons.append("no_lake_manifest")
    if not pinned:
        reasons.append("unpinned_toolchain")
    return EnvironmentCertification(
        repo=repo,
        commit=commit,
        environment_sha256=environment_sha256,
        lean_toolchain=toolchain,
        lake_manifest_present=manifest_present,
        certified=not reasons,
        detail=",".join(reasons),
    )


def _row_ref(row: Any) -> str:
    if isinstance(row, Mapping):
        repo = row.get("repo")
        location = row.get("location")
        if isinstance(repo, Mapping):
            remote = str(repo.get("remote") or "").strip()
            commit = str(repo.get("commit") or "").strip()
            path = ""
            if isinstance(location, Mapping):
                path = str(location.get("path") or "").strip()
            ref = "@".join(part for part in (remote, commit) if part)
            if ref and path:
                return f"{ref}:{path}"
            if ref:
                return ref
        row_id = row.get("id")
        if isinstance(row_id, str) and row_id.strip():
            return row_id.strip()
    return "<unidentified-row>"


def _row_string(row: Mapping[str, Any], field_name: str, default: str | None) -> str | None:
    value = row.get(field_name)
    if value is None:
        return default
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _resolve_toolchain(lean_toolchain: str | None, lean_version: str) -> str:
    if lean_toolchain:
        return lean_toolchain
    if lean_version.startswith("leanprover/lean4:"):
        return lean_version
    if lean_version:
        return f"leanprover/lean4:{lean_version}"
    return ""


def _is_pinned_toolchain(toolchain: str) -> bool:
    return bool(_PINNED_TOOLCHAIN_RE.match(toolchain.strip()))


def _is_lean3(lean_version: str) -> bool:
    value = lean_version.strip().lower()
    if not value:
        return False
    if "lean4" in value or value.startswith("v4") or value.startswith("4."):
        return False
    return bool(_LEAN3_RE.search(value))


def _classify_error(message: str) -> QuarantineReason:
    for needle, reason in _ERROR_REASONS:
        if needle in message:
            return reason
    return "ingest_error"
