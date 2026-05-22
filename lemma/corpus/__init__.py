"""JSONL corpus rows and replay for accepted Lean proofs."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lemma.common.config import LemmaSettings
from lemma.graph import RowDependencies, RowGraph, build_dependencies, build_row_graph
from lemma.lean.proof_identity import proof_identity
from lemma.lean.sandbox import VerifyResult
from lemma.lean.verify_runner import run_lean_verify
from lemma.license import license_state_for
from lemma.quality import RowQuality, build_row_quality
from lemma.submissions import LemmaSubmission
from lemma.task_activation import task_reward_eligibility
from lemma.tasks import LemmaTask, SourceRef


class VerificationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    verifier_version: str = "lemma-lean-v1"
    elapsed_ms: int | None = None
    reason: str | None = None


class CorpusRow(BaseModel):
    """One replayable accepted proof row."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    row_id: str = ""
    task_id: str
    task_version: int
    theorem_name: str
    type_expr: str
    statement: str
    imports: tuple[str, ...]
    lean_toolchain: str
    mathlib_rev: str
    policy: str = "restricted_helpers"
    target_sha256: str
    axiom_set: list[str] = Field(default_factory=list)
    proof_script: str
    proof_sha256: str
    proof_term_hash: str | None = None
    proof_identity: str = ""
    proof_identity_source: str = "script_sha256"
    proof_identity_strength: str = "weak"
    full_reward_eligible: bool = False
    solver_hotkey: str
    validator_hotkey: str
    epoch: int | None = None
    tempo: int | None = None
    commit_block: int | None = Field(default=None, ge=0)
    reveal_block: int | None = Field(default=None, ge=0)
    drand_round: int | None = Field(default=None, ge=0)
    active_K: int | None = None
    queue_position: int | None = None
    queue_depth: int | None = None
    frontier_depth: int | None = None
    ema_solve_rate: float | None = None
    source_stream: str
    source_ref: SourceRef
    source_license: str
    accepted_at: str
    rewarded: bool
    quality: RowQuality = Field(default_factory=RowQuality)
    dependencies: RowDependencies = Field(default_factory=RowDependencies)
    graph: RowGraph | None = None
    verification: VerificationSummary
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_row(self) -> CorpusRow:
        if self.schema_version != 1:
            raise ValueError("corpus row schema_version must be 1")
        if not self.verification.passed:
            raise ValueError("failed proofs are not corpus rows")
        if not self.proof_identity:
            self.proof_identity = self.proof_term_hash or self.proof_sha256
        if self.graph is None:
            task = self.to_task()
            self.graph = build_row_graph(
                task=task,
                proof_identity=self.proof_identity,
                proof_sha256=self.proof_sha256,
                solver_hotkey=self.solver_hotkey,
                validator_hotkey=self.validator_hotkey,
            )
        expected = row_id_for(
            target_sha256=self.target_sha256,
            proof_sha256=self.proof_sha256,
            solver_hotkey=self.solver_hotkey,
            validator_hotkey=self.validator_hotkey,
        )
        if self.row_id and self.row_id != expected:
            raise ValueError(f"row_id mismatch: got {expected}, expected {self.row_id}")
        self.row_id = expected
        return self

    def to_task(self) -> LemmaTask:
        stub = "\n".join(
            [
                *(f"import {name}" for name in self.imports),
                "",
                "namespace Submission",
                "",
                f"theorem {self.theorem_name} : {self.type_expr} := by",
                "  sorry",
                "",
                "end Submission",
                "",
            ]
        )
        return LemmaTask(
            id=self.task_id,
            task_version=self.task_version,
            title=str(self.metadata.get("title") or self.task_id),
            source_stream=self.source_stream,  # type: ignore[arg-type]
            source_ref=self.source_ref,
            source_license=self.source_license,
            imports=self.imports,
            theorem_name=self.theorem_name,
            type_expr=self.type_expr,
            statement=self.statement,
            submission_stub=stub,
            lean_toolchain=self.lean_toolchain,
            mathlib_rev=self.mathlib_rev,
            policy=self.policy,
            target_sha256=self.target_sha256,
            queue_position=self.queue_position,
            queue_depth=self.queue_depth or 0,
            frontier_depth=self.frontier_depth,
            metadata=dict(self.metadata),
        )


def row_id_for(*, target_sha256: str, proof_sha256: str, solver_hotkey: str, validator_hotkey: str) -> str:
    payload = "\n".join([target_sha256, proof_sha256, solver_hotkey, validator_hotkey])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_PRIVATE_METADATA_VALUE = re.compile(
    "|".join(
        (
            re.escape("/" + "Users/"),
            "ro" + "ot@",
            r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
            "AGENT" + r"[_ ]STATE",
            "Agent" + " State",
        )
    ),
    re.IGNORECASE,
)


def _public_metadata_value(value: Any) -> Any | None:
    if isinstance(value, str):
        return None if _PRIVATE_METADATA_VALUE.search(value) else value
    if isinstance(value, dict):
        return _public_metadata(value)
    if isinstance(value, (list, tuple)):
        return [item for raw in value if (item := _public_metadata_value(raw)) is not None]
    return value


def _public_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    blocked_keys = ("path", "file", "dir", "log", "workspace", "secret", "token", "key", "host", "ip", "ssh")
    out: dict[str, Any] = {}
    for key, value in metadata.items():
        lower = key.lower()
        if any(part in lower for part in blocked_keys):
            continue
        public_value = _public_metadata_value(value)
        if public_value is not None:
            out[key] = public_value
    return out


def build_corpus_row(
    task: LemmaTask,
    submission: LemmaSubmission,
    result: VerifyResult,
    *,
    validator_hotkey: str,
    rewarded: bool,
    epoch: int | None = None,
    tempo: int | None = None,
    proof_term_hash: str | None = None,
    structural_fingerprint: str | None = None,
    accepted_at: str | None = None,
    axiom_set: list[str] | None = None,
    active_K: int | None = None,
    ema_solve_rate: float | None = None,
    proof_identity_source: str = "script_sha256",
) -> CorpusRow:
    """Create a replayable corpus row from an accepted submission."""
    term_hash = proof_term_hash or result.proof_term_hash
    structural = structural_fingerprint or result.structural_fingerprint
    identity = proof_identity(
        proof_sha256=submission.proof_sha256,
        proof_term_hash=term_hash,
        structural_fingerprint=structural,
        proof_script=submission.proof_script,
    )
    source = identity.source if term_hash is not None or structural is not None else proof_identity_source
    if source == "script_sha256":
        source = identity.source
    eligibility = task_reward_eligibility(task)
    dependencies = build_dependencies(task, kernel_dependencies=result.kernel_dependencies)
    license_state = license_state_for(task.source_license, str(task.metadata.get("license_state") or ""))
    quality = build_row_quality(
        triviality_checked=task.triviality_status != "unknown" or bool(task.metadata.get("triviality_checked")),
        baseline_solvers_failed=not bool(task.metadata.get("baseline_solved")),
        difficulty_band=task.difficulty_band,
        near_duplicate_score=float(task.metadata.get("near_duplicate_score") or 0.0),
        dependency_depth=dependencies.dependency_depth,
        license_state=license_state,
        proof_identity_strength=identity.strength,
        model_lift_release=task.metadata.get("model_lift_release"),
    )
    row_metadata = {"title": task.title, **_public_metadata(task.metadata)}
    if result.declaration_fingerprints:
        row_metadata["declaration_fingerprints"] = dict(sorted(result.declaration_fingerprints.items()))
    return CorpusRow(
        task_id=task.id,
        task_version=task.task_version,
        theorem_name=task.theorem_name,
        type_expr=task.type_expr,
        statement=task.statement,
        imports=task.imports,
        lean_toolchain=task.lean_toolchain,
        mathlib_rev=task.mathlib_rev,
        policy=task.policy,
        target_sha256=task.target_sha256,
        proof_script=submission.proof_script,
        proof_sha256=submission.proof_sha256,
        proof_term_hash=term_hash,
        proof_identity=identity.value,
        proof_identity_source=source,
        proof_identity_strength=identity.strength,
        full_reward_eligible=(
            rewarded and eligibility.eligible and identity.strength == "strong" and quality.useful_verified_row
        ),
        solver_hotkey=submission.solver_hotkey,
        validator_hotkey=validator_hotkey,
        epoch=epoch,
        tempo=tempo,
        commit_block=submission.commit_block,
        reveal_block=(
            int(submission.metadata["reveal_block"])
            if isinstance(submission.metadata.get("reveal_block"), int)
            else None
        ),
        drand_round=submission.drand_round,
        active_K=active_K,
        queue_position=task.queue_position,
        queue_depth=task.queue_depth,
        frontier_depth=task.frontier_depth,
        ema_solve_rate=ema_solve_rate,
        source_stream=task.source_stream,
        source_ref=task.source_ref,
        source_license=task.source_license,
        accepted_at=accepted_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        rewarded=rewarded,
        quality=quality,
        dependencies=dependencies,
        graph=build_row_graph(
            task=task,
            proof_identity=identity.value,
            proof_sha256=submission.proof_sha256,
            solver_hotkey=submission.solver_hotkey,
            validator_hotkey=validator_hotkey,
        ),
        axiom_set=axiom_set or [],
        verification=VerificationSummary(
            passed=result.passed,
            reason=None if result.passed else result.reason,
            elapsed_ms=int(result.build_seconds * 1000) if result.build_seconds else None,
        ),
        metadata=row_metadata,
    )


def write_jsonl(rows: Iterable[CorpusRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(row.model_dump_json(exclude_none=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[CorpusRow]:
    rows: list[CorpusRow] = []
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(CorpusRow.model_validate_json(line))
        except ValueError as e:
            raise ValueError(f"{path}:{no}: invalid corpus row: {e}") from e
    return rows


def validate_jsonl(path: Path) -> int:
    return len(read_jsonl(path))


def build_corpus_index(corpus_dir: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    row_count = 0
    for path in sorted(corpus_dir.glob("*.jsonl")):
        raw = path.read_bytes()
        rows = read_jsonl(path)
        row_count += len(rows)
        files.append(
            {
                "path": path.name,
                "rows": len(rows),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    return {
        "schema_version": 1,
        "row_count": row_count,
        "files": files,
        "updated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def write_corpus_index(corpus_dir: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(build_corpus_index(corpus_dir), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def benchmark_record(row: CorpusRow) -> dict[str, Any]:
    """Return the stable researcher-facing view of one accepted proof."""
    return {
        "schema_version": 1,
        "row_id": row.row_id,
        "task": {
            "id": row.task_id,
            "version": row.task_version,
            "theorem_name": row.theorem_name,
            "type_expr": row.type_expr,
            "statement": row.statement,
            "imports": list(row.imports),
            "lean_toolchain": row.lean_toolchain,
            "mathlib_rev": row.mathlib_rev,
            "policy": row.policy,
            "target_sha256": row.target_sha256,
            "queue_position": row.queue_position,
            "queue_depth": row.queue_depth,
            "frontier_depth": row.frontier_depth,
        },
        "proof": {
            "script": row.proof_script,
            "sha256": row.proof_sha256,
            "term_hash": row.proof_term_hash,
            "identity": row.proof_identity,
            "identity_source": row.proof_identity_source,
            "identity_strength": row.proof_identity_strength,
            "axiom_set": row.axiom_set,
        },
        "source": {
            "stream": row.source_stream,
            "ref": row.source_ref.model_dump(exclude_none=True),
            "license": row.source_license,
        },
        "reward": {
            "rewarded": row.rewarded,
            "epoch": row.epoch,
            "tempo": row.tempo,
            "active_K": row.active_K,
            "ema_solve_rate": row.ema_solve_rate,
        },
        "verification": row.verification.model_dump(exclude_none=True),
        "quality": row.quality.model_dump(exclude_none=True),
        "dependencies": row.dependencies.model_dump(exclude_none=True),
        "graph": row.graph.model_dump(exclude_none=True) if row.graph is not None else None,
        "provenance": {
            "accepted_at": row.accepted_at,
            "solver_hotkey": row.solver_hotkey,
            "validator_hotkey": row.validator_hotkey,
        },
        "metadata": _public_metadata(row.metadata),
    }


def _count(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def write_benchmark_export(
    corpus_dir: Path,
    output_path: Path,
    *,
    index_path: Path | None = None,
    rewarded_only: bool = False,
    useful_only: bool = False,
    license_filter: str | None = None,
    exclude_near_duplicates: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Write accepted corpus proofs as a compact benchmark/training JSONL export."""
    records: list[dict[str, Any]] = []
    source_files: list[dict[str, Any]] = []
    for path in sorted(corpus_dir.glob("*.jsonl")):
        raw = path.read_bytes()
        rows = read_jsonl(path)
        source_files.append({"path": path.name, "rows": len(rows), "sha256": hashlib.sha256(raw).hexdigest()})
        for row in rows:
            if rewarded_only and not row.rewarded:
                continue
            if useful_only and not row.quality.useful_verified_row:
                continue
            if license_filter:
                wanted = license_filter.strip().lower()
                if wanted == "commercial-safe":
                    if row.quality.license_state not in {"clean_open", "attribution_required"}:
                        continue
                elif row.quality.license_state != wanted and row.source_license.lower() != wanted:
                    continue
            if exclude_near_duplicates and row.quality.near_duplicate_score >= 0.9:
                continue
            records.append(benchmark_record(row))
            if limit is not None and len(records) >= limit:
                break
        if limit is not None and len(records) >= limit:
            break

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "".join(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )
    raw_export = output_path.read_bytes()
    index = {
        "schema_version": 1,
        "format": "lemma-benchmark-export-v1",
        "row_count": len(records),
        "rewarded_only": rewarded_only,
        "useful_only": useful_only,
        "license_filter": license_filter,
        "exclude_near_duplicates": exclude_near_duplicates,
        "limit": limit,
        "export": {"path": output_path.name, "sha256": hashlib.sha256(raw_export).hexdigest()},
        "source_files": source_files,
        "source_streams": _count(str(record["source"]["stream"]) for record in records),
        "mathlib_revs": _count(str(record["task"]["mathlib_rev"]) for record in records),
        "updated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    if index_path is not None:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return index


def replay_jsonl(settings: LemmaSettings, path: Path) -> list[VerifyResult]:
    results: list[VerifyResult] = []
    for row in read_jsonl(path):
        task = row.to_task()
        results.append(
            run_lean_verify(
                settings,
                verify_timeout_s=settings.lean_verify_timeout_s,
                problem=task.to_problem(),
                proof_script=row.proof_script,
                submission_policy=row.policy,
            ),
        )
    return results


from lemma.corpus.rows import CorpusRowV2, build_corpus_row_v2  # noqa: E402

__all__ = [
    "CorpusRow",
    "CorpusRowV2",
    "VerificationSummary",
    "benchmark_record",
    "build_corpus_index",
    "build_corpus_row",
    "build_corpus_row_v2",
    "read_jsonl",
    "replay_jsonl",
    "row_id_for",
    "validate_jsonl",
    "write_benchmark_export",
    "write_corpus_index",
    "write_jsonl",
]
