"""Build the real-task Proof Atlas artifacts from public inputs.

Inputs are all public and reproducible:

* the pinned task registry (full real task bundles with source/environment);
* accepted proof rows (canonical network output, one JSONL row per artifact);
* optional source ingest reports (per-source provenance and quarantine counts).

Outputs are public artifacts written into the Proof Atlas layout. Nothing here
uploads, commits, or writes to chain - that stays in the publisher script.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from lemma.tasks import LemmaTask, TaskRegistry, load_task_registry

SCHEMA_VERSION = 1


def _stable_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class TaskBundle(BaseModel):
    """A pinned real task with the public metadata needed to find and reproduce it.

    This is the public face of one task: where it came from, the exact target,
    the certified environment, and the command a validator runs to reproduce the
    check. It carries no operator state, secrets, or local paths.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str
    task_version: int
    title: str
    task_format: str
    task_class: str
    source_value: str
    source_stream: str
    source_ref: dict[str, Any]
    source_license: str
    imports: list[str]
    allowed_files: list[str]
    allowed_imports: list[str]
    theorem_name: str
    type_expr: str
    statement: str
    target_sha256: str
    target_type_sha256: str
    environment_sha256: str | None
    lean_toolchain: str
    mathlib_rev: str
    reproduction_command: str
    activation_status: str
    difficulty_band: str


class EnvironmentEntry(BaseModel):
    """A certified validation environment keyed by its hash."""

    model_config = ConfigDict(extra="forbid")

    environment_sha256: str
    lean_toolchain: str
    mathlib_rev: str
    source_kinds: list[str]
    task_ids: list[str]
    task_count: int


class SolvedEntry(BaseModel):
    """A real task that has an accepted, rewarded proof."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    task_format: str
    source_ref: dict[str, Any]
    source_license: str
    target_sha256: str
    target_type_sha256: str
    environment_sha256: str | None
    reproduction_command: str
    artifact_kind: str
    artifact_sha256: str
    proof_identity: str
    proof_identity_strength: str
    miner_hotkey: str
    validator_hotkey: str
    block: int
    tempo: int | None
    rewarded: bool
    apply_instructions: str


class RealTaskSnapshot(BaseModel):
    """A dry-run real-task Proof Atlas snapshot manifest.

    Records every file the snapshot would write with its SHA256, plus headline
    counts. This is the object a reviewer inspects before any upload.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    netuid: str
    registry_sha256: str
    bundle_count: int
    solved_count: int
    environment_count: int
    source_count: int
    files: dict[str, str] = Field(default_factory=dict)


def build_task_bundle(task: LemmaTask) -> TaskBundle:
    """Project one task into its public, reproducible bundle."""
    return TaskBundle(
        task_id=task.id,
        task_version=task.task_version,
        title=task.title,
        task_format=task.task_format,
        task_class=task.task_class,
        source_value=task.source_value,
        source_stream=task.source_stream,
        source_ref=task.source_ref.model_dump(exclude_none=True),
        source_license=task.source_license,
        imports=list(task.imports),
        allowed_files=list(task.allowed_files),
        allowed_imports=list(task.allowed_imports),
        theorem_name=task.theorem_name,
        type_expr=task.type_expr,
        statement=task.statement,
        target_sha256=task.target_sha256,
        target_type_sha256=task.target_type_sha256,
        environment_sha256=task.environment_sha256,
        lean_toolchain=task.lean_toolchain,
        mathlib_rev=task.mathlib_rev,
        reproduction_command=task.reproduction_command,
        activation_status=task.activation_status,
        difficulty_band=task.difficulty_band,
    )


def build_task_bundles(registry: TaskRegistry) -> list[TaskBundle]:
    """Build the deterministic list of public task bundles for a registry."""
    bundles = [build_task_bundle(task) for task in registry.tasks]
    bundles.sort(key=lambda bundle: bundle.task_id)
    return bundles


def build_env_index(bundles: Sequence[TaskBundle]) -> list[EnvironmentEntry]:
    """Group certified environments by hash across the task bundles."""
    grouped: dict[str, dict[str, Any]] = {}
    for bundle in bundles:
        env_hash = bundle.environment_sha256
        if not env_hash:
            continue
        entry = grouped.setdefault(
            env_hash,
            {
                "lean_toolchain": bundle.lean_toolchain,
                "mathlib_rev": bundle.mathlib_rev,
                "source_kinds": set(),
                "task_ids": set(),
            },
        )
        source_kind = str(bundle.source_ref.get("kind") or bundle.source_stream)
        entry["source_kinds"].add(source_kind)
        entry["task_ids"].add(bundle.task_id)
    entries = [
        EnvironmentEntry(
            environment_sha256=env_hash,
            lean_toolchain=str(data["lean_toolchain"]),
            mathlib_rev=str(data["mathlib_rev"]),
            source_kinds=sorted(data["source_kinds"]),
            task_ids=sorted(data["task_ids"]),
            task_count=len(data["task_ids"]),
        )
        for env_hash, data in grouped.items()
    ]
    entries.sort(key=lambda entry: entry.environment_sha256)
    return entries


def apply_instructions(bundle: TaskBundle, *, artifact_kind: str) -> str:
    """Human-facing instructions for applying an accepted artifact to its source."""
    source = bundle.source_ref
    where = source.get("url") or source.get("name") or bundle.source_stream
    commit = source.get("commit")
    pin = f" at commit {commit}" if commit else ""
    repro = bundle.reproduction_command or "the validator reproduction command"
    if artifact_kind == "patch":
        target_file = source.get("path") or (bundle.allowed_files[0] if bundle.allowed_files else "the target file")
        return (
            f"Check out {where}{pin}. Apply the accepted patch to {target_file} "
            f"(editable files: {', '.join(bundle.allowed_files) or 'see bundle'}). "
            f"Then reproduce the validator check with: {repro}."
        )
    imports = ", ".join(bundle.imports) or "the pinned imports"
    return (
        f"In a Lean project pinned to toolchain {bundle.lean_toolchain} and mathlib {bundle.mathlib_rev} "
        f"(environment {bundle.environment_sha256 or 'see bundle'}), importing only {imports}, "
        f"prove `{bundle.theorem_name} : {bundle.type_expr}` using the accepted proof. "
        f"Then reproduce the validator check with: {repro}. Original source: {where}{pin}."
    )


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _normalize_accepted_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Read an accepted row into one shape, handling v1 (flat) and v2 (nested).

    The canonical on-disk proof row is the flat v1 row with top-level fields.
    The v2 export row nests the same facts under ``accepted_artifact``,
    ``provenance``, and ``metadata``. We prefer the flat fields and fall back to
    the nested ones so the solved ledger works for either.
    """
    artifact = _as_mapping(row.get("accepted_artifact"))
    provenance = _as_mapping(row.get("provenance"))
    metadata = _as_mapping(row.get("metadata"))
    artifact_kind = str(row.get("artifact_kind") or artifact.get("kind") or "proof")
    rewarded = bool(row["rewarded"]) if "rewarded" in row else bool(metadata.get("rewarded", False))
    if artifact_kind == "patch":
        artifact_sha256 = row.get("patch_sha256") or row.get("proof_sha256") or artifact.get("artifact_sha256") or ""
    else:
        artifact_sha256 = row.get("proof_sha256") or artifact.get("artifact_sha256") or ""
    block = row.get("commit_block")
    if block is None:
        block = row.get("block")
    if block is None:
        block = provenance.get("block")
    return {
        "task_id": str(row.get("task_id") or ""),
        "artifact_kind": artifact_kind,
        "artifact_sha256": str(artifact_sha256),
        "proof_identity": str(row.get("proof_identity") or artifact.get("proof_identity") or ""),
        "proof_identity_strength": str(
            row.get("proof_identity_strength") or artifact.get("proof_identity_strength") or "unknown"
        ),
        "miner_hotkey": str(row.get("solver_hotkey") or provenance.get("miner_hotkey") or ""),
        "validator_hotkey": str(row.get("validator_hotkey") or provenance.get("validator_hotkey") or ""),
        "block": int(block or 0),
        "tempo": int(row["tempo"]) if isinstance(row.get("tempo"), int) else None,
        "rewarded": rewarded,
    }


def build_solved_ledger(
    accepted_rows: Iterable[Mapping[str, Any]],
    bundles: Sequence[TaskBundle],
    *,
    rewarded_only: bool = True,
) -> list[SolvedEntry]:
    """Join accepted proof rows to their task bundles to form the solved ledger.

    Only rewarded rows (the first verified unique artifact per slot) count as a
    task being solved by default; valid alternates are excluded.
    """
    bundles_by_id = {bundle.task_id: bundle for bundle in bundles}
    entries: list[SolvedEntry] = []
    seen: set[str] = set()
    for raw in accepted_rows:
        row = _normalize_accepted_row(raw)
        task_id = row["task_id"]
        if not task_id or task_id in seen:
            continue
        if rewarded_only and not row["rewarded"]:
            continue
        bundle = bundles_by_id.get(task_id)
        if bundle is None:
            continue
        seen.add(task_id)
        artifact_kind = row["artifact_kind"]
        entries.append(
            SolvedEntry(
                task_id=task_id,
                task_format=bundle.task_format,
                source_ref=bundle.source_ref,
                source_license=bundle.source_license,
                target_sha256=bundle.target_sha256,
                target_type_sha256=bundle.target_type_sha256,
                environment_sha256=bundle.environment_sha256,
                reproduction_command=bundle.reproduction_command,
                artifact_kind=artifact_kind,
                artifact_sha256=row["artifact_sha256"],
                proof_identity=row["proof_identity"],
                proof_identity_strength=row["proof_identity_strength"],
                miner_hotkey=row["miner_hotkey"],
                validator_hotkey=row["validator_hotkey"],
                block=row["block"],
                tempo=row["tempo"],
                rewarded=row["rewarded"],
                apply_instructions=apply_instructions(bundle, artifact_kind=artifact_kind),
            )
        )
    entries.sort(key=lambda entry: entry.task_id)
    return entries


def read_accepted_rows(source: Path) -> list[dict[str, Any]]:
    """Read accepted proof rows from a JSONL file or an accepted/ directory."""
    files: list[Path]
    if source.is_dir():
        files = sorted(source.glob("epoch-*.jsonl"))
    elif source.is_file():
        files = [source]
    else:
        return []
    rows: list[dict[str, Any]] = []
    for path in files:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def load_atlas_registry_union(registries_dir: Path) -> TaskRegistry | None:
    """Merge the pinned registries in a Proof Atlas into one task set.

    Newer registries win on task-id collisions, so historical solves still
    resolve to a bundle while the current pinning stays authoritative. Returns
    ``None`` when no registry is present, so the real-task layer stays optional.
    """
    if not registries_dir.is_dir():
        return None
    ordered: list[Path] = []
    index_path = registries_dir / "index.json"
    if index_path.is_file():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        registries = index.get("registries") if isinstance(index, dict) else None
        if isinstance(registries, dict):

            def _tempo_key(item: tuple[str, Any]) -> int:
                try:
                    return int(item[0])
                except (TypeError, ValueError):
                    return -1

            for _tempo, meta in sorted(registries.items(), key=_tempo_key, reverse=True):
                path = meta.get("path") if isinstance(meta, dict) else None
                if isinstance(path, str):
                    candidate = registries_dir / path
                    if candidate.is_file() and candidate not in ordered:
                        ordered.append(candidate)
    if not ordered:
        skip = {"index.json", "current-index.json", "snapshot.json"}
        ordered = sorted(path for path in registries_dir.glob("*.json") if path.name not in skip)
    if not ordered:
        return None
    seen: set[str] = set()
    tasks: list[LemmaTask] = []
    digest = hashlib.sha256()
    for path in ordered:
        registry = load_task_registry(path.read_bytes())
        digest.update(registry.sha256.encode("utf-8"))
        for task in registry.tasks:
            if task.id not in seen:
                seen.add(task.id)
                tasks.append(task)
    if not tasks:
        return None
    sha256 = load_task_registry(ordered[0].read_bytes()).sha256 if len(ordered) == 1 else digest.hexdigest()
    return TaskRegistry(schema_version=1, tasks=tuple(tasks), sha256=sha256)


def read_source_reports(sources_dir: Path) -> list[dict[str, Any]]:
    """Read source ingest reports dropped into the sources/ directory."""
    if not sources_dir.is_dir():
        return []
    skip = {"index.json", "snapshot.json"}
    reports: list[dict[str, Any]] = []
    for path in sorted(sources_dir.glob("*.json")):
        if path.name in skip:
            continue
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            reports.append(parsed)
    return reports


def _load_source_reports(reports: Sequence[Path]) -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = []
    for path in reports:
        if not path.is_file():
            continue
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            loaded.append(parsed)
    return loaded


def build_real_task_snapshot(
    *,
    netuid: str,
    registry: TaskRegistry,
    accepted_rows: Iterable[Mapping[str, Any]],
    source_reports: Sequence[Mapping[str, Any]] = (),
) -> tuple[RealTaskSnapshot, dict[str, Any]]:
    """Assemble the real-task Atlas artifacts and a snapshot manifest in memory.

    Returns the manifest plus a mapping of relative file path to its JSON-ready
    payload, so the caller can preview, hash, or write the snapshot.
    """
    bundles = build_task_bundles(registry)
    rows = list(accepted_rows)
    solved = build_solved_ledger(rows, bundles)
    environments = build_env_index(bundles)

    bundle_index = {
        "schema_version": SCHEMA_VERSION,
        "netuid": netuid,
        "registry_sha256": registry.sha256,
        "bundle_count": len(bundles),
        "bundles": [bundle.model_dump() for bundle in bundles],
    }
    solved_ledger = {
        "schema_version": SCHEMA_VERSION,
        "netuid": netuid,
        "solved_count": len(solved),
        "solved": [entry.model_dump() for entry in solved],
    }
    env_index = {
        "schema_version": SCHEMA_VERSION,
        "netuid": netuid,
        "environment_count": len(environments),
        "environments": [entry.model_dump() for entry in environments],
    }
    sources_index = {
        "schema_version": SCHEMA_VERSION,
        "netuid": netuid,
        "source_count": len(source_reports),
        "sources": [dict(report) for report in source_reports],
    }

    payloads: dict[str, Any] = {
        f"tasks/{netuid}/bundles/index.json": bundle_index,
        f"proofs/{netuid}/solved-ledger.json": solved_ledger,
        f"envs/{netuid}/index.json": env_index,
        f"sources/{netuid}/index.json": sources_index,
    }
    files = {relpath: _sha256_text(_stable_json(payload)) for relpath, payload in payloads.items()}
    manifest = RealTaskSnapshot(
        netuid=netuid,
        registry_sha256=registry.sha256,
        bundle_count=len(bundles),
        solved_count=len(solved),
        environment_count=len(environments),
        source_count=len(source_reports),
        files=files,
    )
    return manifest, payloads


def write_real_task_snapshot(
    repo: Path,
    *,
    netuid: str,
    registry: TaskRegistry,
    accepted_rows: Iterable[Mapping[str, Any]],
    source_reports: Sequence[Mapping[str, Any]] = (),
) -> RealTaskSnapshot:
    """Write the real-task Atlas artifacts under `repo` and return the manifest."""
    manifest, payloads = build_real_task_snapshot(
        netuid=netuid,
        registry=registry,
        accepted_rows=accepted_rows,
        source_reports=source_reports,
    )
    for relpath, payload in payloads.items():
        target = repo / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_stable_json(payload) + "\n", encoding="utf-8")
    (repo / f"tasks/{netuid}/bundles/snapshot.json").write_text(
        _stable_json(manifest.model_dump()) + "\n",
        encoding="utf-8",
    )
    return manifest


def build_snapshot_from_paths(
    *,
    netuid: str,
    registry_path: Path,
    accepted_path: Path,
    source_report_paths: Sequence[Path] = (),
) -> tuple[RealTaskSnapshot, dict[str, Any]]:
    """Load public inputs from disk and build an in-memory snapshot (dry run)."""
    registry = load_task_registry(registry_path.read_bytes())
    rows = read_accepted_rows(accepted_path)
    reports = _load_source_reports(list(source_report_paths))
    return build_real_task_snapshot(
        netuid=netuid,
        registry=registry,
        accepted_rows=rows,
        source_reports=reports,
    )
