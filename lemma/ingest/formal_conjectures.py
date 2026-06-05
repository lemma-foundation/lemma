"""Formal Conjectures source ingester.

Turn pre-extracted Formal Conjectures records into safely-restated isolated-proof
task candidates. Each record is a public statement description (category, target
type, imports, the module that exposes the original sorry-backed declaration).
This ingester does NOT parse the Formal Conjectures Lean repo; a separate
extractor produces the records. Here we only filter and package.

Packaging rules enforced (roadmap):

* allowed v1 categories only (solved / textbook / API / test);
* a clean restatement that does not import the module exposing the original
  declaration, so a miner cannot win by ``exact original_problem``;
* reject statements that cannot be cleanly separated from the original.

New Formal Conjectures candidates default to held-out *benchmark* framing (no
reward); an operator opts a record into paid work explicitly. This keeps paid
work and benchmark claims separate by default.

Pure data tooling: no proof generation, no model calls.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

from lemma.ingest.baseline import BaselineProber
from lemma.ingest.report import (
    EnvironmentCertification,
    IngestCandidate,
    IngestReport,
    IngestResult,
    QuarantinedRow,
)
from lemma.tasks import LemmaTask, SourceRef, target_type_sha256

#: Allowed v1 categories. Everything else is quarantined as unsafe_category.
SAFE_CATEGORIES: frozenset[str] = frozenset({"solved", "textbook", "api", "test"})

DEFAULT_TOOLCHAIN = "leanprover/lean4:v4.30.0-rc2"

_PINNED_TOOLCHAIN_RE = re.compile(r"^leanprover/lean4:v\d+\.\d+\.\d+(?:-rc\d+)?$")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.']*")
#: Clean task namespace + theorem name miners prove against.
_SUBMISSION_NAMESPACE = "Submission"
_TARGET_NAME = "target"


def formal_conjecture_environment_sha256(toolchain: str, mathlib_rev: str, imports: Sequence[str]) -> str:
    """Hash the pinned isolated-proof environment so validators reproduce it."""
    parts = [toolchain.strip(), mathlib_rev.strip(), *sorted(i.strip() for i in imports)]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def ingest_formal_conjecture_records(
    records: Sequence[Any],
    *,
    source_license: str,
    mathlib_rev: str,
    default_toolchain: str | None = None,
    allowed_categories: frozenset[str] = SAFE_CATEGORIES,
    default_paid: bool = False,
    allow_unstable_toolchain: bool = False,
    baseline_prober: BaselineProber | None = None,
) -> IngestResult:
    """Package Formal Conjectures records into restated isolated-proof tasks."""
    report = IngestReport(source_stream="formal_conjectures", total_rows=len(records))
    result = IngestResult(report=report)
    seen_targets: set[str] = set()
    env_seen: set[tuple[str, str]] = set()

    for index, record in enumerate(records):
        outcome = _ingest_record(
            record,
            source_license=source_license,
            mathlib_rev=mathlib_rev,
            default_toolchain=default_toolchain or DEFAULT_TOOLCHAIN,
            allowed_categories=allowed_categories,
            default_paid=default_paid,
            allow_unstable_toolchain=allow_unstable_toolchain,
            baseline_prober=baseline_prober,
            seen_targets=seen_targets,
            queue_position=index,
        )
        if outcome.environment is not None:
            key = (outcome.environment.repo, outcome.environment.commit)
            if key not in env_seen:
                env_seen.add(key)
                report.environments.append(outcome.environment)
        if outcome.quarantine is not None:
            report.quarantined.append(outcome.quarantine)
            continue
        assert outcome.task is not None and outcome.candidate is not None
        seen_targets.add(outcome.task.target_sha256)
        result.tasks.append(outcome.task)
        report.accepted.append(outcome.candidate)

    return result


class _Outcome:
    def __init__(
        self,
        *,
        task: LemmaTask | None = None,
        candidate: IngestCandidate | None = None,
        quarantine: QuarantinedRow | None = None,
        environment: EnvironmentCertification | None = None,
    ) -> None:
        self.task = task
        self.candidate = candidate
        self.quarantine = quarantine
        self.environment = environment


def _ingest_record(
    record: Any,
    *,
    source_license: str,
    mathlib_rev: str,
    default_toolchain: str,
    allowed_categories: frozenset[str],
    default_paid: bool,
    allow_unstable_toolchain: bool,
    baseline_prober: BaselineProber | None,
    seen_targets: set[str],
    queue_position: int,
) -> _Outcome:
    row_ref = _row_ref(record)

    if not isinstance(record, Mapping):
        return _Outcome(quarantine=QuarantinedRow(row_ref=row_ref, reason="malformed_row"))
    if not source_license.strip():
        return _Outcome(quarantine=QuarantinedRow(row_ref=row_ref, reason="missing_license"))

    category = _str(record.get("category"))
    if category not in allowed_categories:
        return _Outcome(quarantine=QuarantinedRow(row_ref=row_ref, reason="unsafe_category", detail=category))

    type_expr = _str(record.get("type_expr"))
    if not type_expr:
        return _Outcome(quarantine=QuarantinedRow(row_ref=row_ref, reason="missing_type_expr"))

    toolchain = _str(record.get("lean_toolchain")) or default_toolchain
    if not allow_unstable_toolchain and not _PINNED_TOOLCHAIN_RE.match(toolchain):
        return _Outcome(quarantine=QuarantinedRow(row_ref=row_ref, reason="unstable_toolchain", detail=toolchain))

    imports = _str_tuple(record.get("imports")) or ("Mathlib",)
    allowed_imports = _str_tuple(record.get("allowed_imports")) or imports
    source_module = _str(record.get("source_module"))
    original_name = _str(record.get("theorem_name"))

    # Cheat guard: the solution must never be able to import the module that
    # exposes the original sorry-backed declaration.
    exposing = {imp for imp in (*imports, *allowed_imports) if source_module and imp == source_module}
    if exposing:
        return _Outcome(quarantine=QuarantinedRow(row_ref=row_ref, reason="exposes_original", detail=source_module))

    if _truthy(record.get("requires_source_import")):
        return _Outcome(quarantine=QuarantinedRow(row_ref=row_ref, reason="requires_source_import"))

    # Cheat guard: the restated target type must not name the original
    # declaration (otherwise it cannot be cleanly separated / restated).
    if original_name and _references(type_expr, original_name):
        return _Outcome(quarantine=QuarantinedRow(row_ref=row_ref, reason="references_original", detail=original_name))

    repo = record.get("repo") if isinstance(record.get("repo"), Mapping) else {}
    remote = _str(repo.get("remote"))
    commit = _str(repo.get("commit"))
    environment_sha256 = formal_conjecture_environment_sha256(toolchain, mathlib_rev, imports)
    environment = EnvironmentCertification(
        repo=remote,
        commit=commit,
        environment_sha256=environment_sha256,
        lean_toolchain=toolchain,
        lake_manifest_present=False,
        certified=bool(_PINNED_TOOLCHAIN_RE.match(toolchain)),
        detail="isolated_proof_env" if _PINNED_TOOLCHAIN_RE.match(toolchain) else "unpinned_toolchain",
    )

    paid = _truthy(record.get("paid")) if record.get("paid") is not None else default_paid
    record_id = _str(record.get("id")) or hashlib.sha256(f"{type_expr}:{category}".encode()).hexdigest()[:12]

    try:
        task = _build_task(
            record_id=record_id,
            title=_str(record.get("title")) or f"Formal Conjecture {category}",
            type_expr=type_expr,
            imports=imports,
            allowed_imports=allowed_imports,
            toolchain=toolchain,
            mathlib_rev=mathlib_rev,
            source_license=source_license,
            source_name=_str(repo.get("name")) or _source_name(remote) or "formal_conjectures",
            source_url=remote,
            source_commit=commit,
            source_path=_str(record.get("source_path")),
            environment_sha256=environment_sha256,
            category=category,
            paid=paid,
            references=_str_tuple(record.get("references")),
            original_name=original_name,
            source_module=source_module,
            queue_position=queue_position,
        )
    except ValueError as e:
        return _Outcome(
            quarantine=QuarantinedRow(row_ref=row_ref, reason="ingest_error", detail=str(e)),
            environment=environment,
        )

    if task.target_sha256 in seen_targets:
        return _Outcome(
            quarantine=QuarantinedRow(row_ref=row_ref, reason="duplicate_target", detail=task.target_sha256),
            environment=environment,
        )

    baseline_status = "unscreened"
    if baseline_prober is not None:
        probe = baseline_prober(task, None)
        if probe.trivial:
            return _Outcome(
                quarantine=QuarantinedRow(row_ref=row_ref, reason="baseline_trivial", detail=probe.tactic),
                environment=environment,
            )
        baseline_status = "nontrivial"

    candidate = IngestCandidate(
        task_id=task.id,
        row_ref=row_ref,
        repo=remote,
        commit=commit,
        path=_str(record.get("source_path")),
        theorem_name=task.theorem_name,
        target_sha256=task.target_sha256,
        target_type_sha256=task.target_type_sha256,
        environment_sha256=task.environment_sha256,
        lean_toolchain=task.lean_toolchain,
        baseline_status=baseline_status,  # type: ignore[arg-type]
    )
    return _Outcome(task=task, candidate=candidate, environment=environment)


def _build_task(
    *,
    record_id: str,
    title: str,
    type_expr: str,
    imports: tuple[str, ...],
    allowed_imports: tuple[str, ...],
    toolchain: str,
    mathlib_rev: str,
    source_license: str,
    source_name: str,
    source_url: str,
    source_commit: str,
    source_path: str,
    environment_sha256: str,
    category: str,
    paid: bool,
    references: tuple[str, ...],
    original_name: str,
    source_module: str,
    queue_position: int,
) -> LemmaTask:
    statement = _restatement(type_expr, imports)
    metadata: dict[str, Any] = {
        "fc_category": category,
        "fc_references": list(references),
    }
    if original_name:
        metadata["fc_original_name"] = original_name
    if source_module:
        metadata["fc_source_module"] = source_module
    if not paid:
        metadata["held_out_benchmark"] = True

    return LemmaTask(
        id=f"lemma.fc.{_safe_id(record_id)}",
        task_version=1,
        title=title,
        task_format="isolated_proof",
        task_class="formal_conjecture",
        source_value="low" if paid else "calibration",
        source_stream="formal_conjectures",
        source_ref=SourceRef(
            kind="formal_conjectures",
            name=source_name,
            url=source_url or None,
            commit=source_commit or None,
            path=source_path or None,
        ),
        source_license=source_license,
        imports=imports,
        allowed_files=("Submission.lean",),
        allowed_imports=allowed_imports,
        theorem_name=_TARGET_NAME,
        type_expr=type_expr,
        statement=statement,
        submission_stub=statement,
        lean_toolchain=toolchain,
        mathlib_rev=mathlib_rev,
        policy="restricted_helpers",
        target_type_sha256=target_type_sha256(type_expr),
        environment_sha256=environment_sha256,
        reproduction_command="lake build",
        queue_position=queue_position,
        activation_status="paid" if paid else "benchmark",
        difficulty_band="medium",
        metadata=metadata,
    )


def _restatement(type_expr: str, imports: tuple[str, ...]) -> str:
    import_lines = [f"import {module}" for module in (imports or ("Mathlib",))]
    return "\n".join(
        [
            *import_lines,
            "",
            f"namespace {_SUBMISSION_NAMESPACE}",
            "",
            f"theorem {_TARGET_NAME} : {type_expr} := by",
            "  sorry",
            "",
            f"end {_SUBMISSION_NAMESPACE}",
            "",
        ]
    )


def _references(type_expr: str, name: str) -> bool:
    short = name.rsplit(".", 1)[-1]
    tokens = set(_IDENT_RE.findall(type_expr))
    return name in tokens or short in tokens


def _row_ref(record: Any) -> str:
    if isinstance(record, Mapping):
        record_id = _str(record.get("id"))
        if record_id:
            return record_id
        type_expr = _str(record.get("type_expr"))
        if type_expr:
            return f"fc:{hashlib.sha256(type_expr.encode()).hexdigest()[:12]}"
    return "<unidentified-record>"


def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _truthy(value: Any) -> bool:
    return value is True


def _source_name(remote: str) -> str:
    trimmed = remote.removesuffix(".git").rstrip("/")
    if "/" in trimmed:
        return trimmed.rsplit("/", 2)[-2] + "/" + trimmed.rsplit("/", 1)[-1]
    return trimmed


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or hashlib.sha256(value.encode()).hexdigest()[:12]
