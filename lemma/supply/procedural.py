"""Production procedural task generation from public source rows and epoch seed."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from lemma.license import license_state_for
from lemma.protocol_invariants import production_supply_rejection_reason
from lemma.supply.mathlib_snapshot import MathlibSnapshotRow
from lemma.supply.triviality_gate import label_from_baseline
from lemma.supply.types import TaskCandidate, lean_stub
from lemma.task_supply import deterministic_queue
from lemma.tasks import LemmaTask, SourceRef

PROCEDURAL_DEPTH2_OPERATOR_VERSION = "lemma-procedural-depth2-v1"
_SEED_VARIANTS = ("and_true_right", "and_true_left", "and_self", "or_false")


@dataclass(frozen=True)
class RejectedProceduralCandidate:
    id: str
    reason: str


@dataclass(frozen=True)
class ProceduralRegistryBuild:
    tasks: tuple[LemmaTask, ...]
    rejected: tuple[RejectedProceduralCandidate, ...]


@dataclass(frozen=True)
class SourcePoolRows:
    rows: tuple[MathlibSnapshotRow, ...]
    sha256: str


def candidates_from_jsonl(path: Path) -> tuple[TaskCandidate, ...]:
    out: list[TaskCandidate] = []
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            out.append(TaskCandidate.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"{path}:{no}: invalid task candidate: {e}") from e
    return tuple(out)


def write_candidates_jsonl(candidates: Iterable[TaskCandidate], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(candidate.model_dump_json(exclude_none=True) + "\n" for candidate in candidates),
        encoding="utf-8",
    )


def source_pool_from_url(source: str, expected_sha256: str | None = None) -> SourcePoolRows:
    raw = _read_source_pool_bytes(source)
    digest = hashlib.sha256(raw).hexdigest()
    expected = _normalize_sha256(expected_sha256)
    if expected and digest != expected:
        raise ValueError(f"source pool sha256 mismatch: got {digest}, expected {expected}")
    return SourcePoolRows(rows=mathlib_rows_from_jsonl_bytes(raw, label=source), sha256=digest)


def mathlib_rows_from_jsonl_bytes(raw: bytes, *, label: str = "<source-pool>") -> tuple[MathlibSnapshotRow, ...]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"{label}: source pool must be UTF-8 JSONL") from e
    out: list[MathlibSnapshotRow] = []
    for no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            out.append(MathlibSnapshotRow.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"{label}:{no}: invalid Mathlib snapshot row: {e}") from e
    if not out:
        raise ValueError("source pool is empty")
    return tuple(out)


def mathlib_rows_from_jsonl(path: Path, *, limit: int | None = None) -> tuple[MathlibSnapshotRow, ...]:
    out: list[MathlibSnapshotRow] = []
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            out.append(MathlibSnapshotRow.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError(f"{path}:{no}: invalid Mathlib snapshot row: {e}") from e
        if limit is not None and len(out) >= limit:
            break
    return tuple(out)


def generate_depth2_candidates_from_mathlib_rows(
    rows: tuple[MathlibSnapshotRow, ...],
    *,
    generation_seed: str,
    anchor_block: int,
    anchor_block_hash: str,
    limit: int | None = None,
) -> tuple[TaskCandidate, ...]:
    if not generation_seed.strip():
        raise ValueError("generation_seed must be non-empty")
    if anchor_block < 0:
        raise ValueError("anchor_block must be non-negative")
    if not anchor_block_hash.strip():
        raise ValueError("anchor_block_hash must be non-empty")
    if not rows:
        raise ValueError("at least one Mathlib snapshot row is required")

    source_pool_hash = _hash_json([_row_payload(row) for row in rows])
    operator_bundle_hash = _hash_json(
        {
            "version": PROCEDURAL_DEPTH2_OPERATOR_VERSION,
            "operators": ("canonicalize_mathlib_snapshot_row", "epoch_bind_procedural_task"),
            "seed_variants": _SEED_VARIANTS,
        }
    )
    ordered = sorted(rows, key=lambda row: _seeded_row_key(generation_seed, row))
    seen: set[str] = set()
    out: list[TaskCandidate] = []
    for row in ordered:
        source_hash = _hash_json(_row_payload(row))
        canonical = _canonical_row_payload(row)
        canonical_hash = _hash_json(canonical)
        if canonical_hash in seen:
            continue
        seen.add(canonical_hash)

        bound = {
            "canonical_hash": canonical_hash,
            "generation_seed": generation_seed,
            "anchor_block": anchor_block,
            "anchor_block_hash": anchor_block_hash,
            "operator_bundle_hash": operator_bundle_hash,
        }
        candidate_hash = _hash_json(bound)
        variant = _SEED_VARIANTS[int(candidate_hash[:8], 16) % len(_SEED_VARIANTS)]
        theorem_name = f"lemma_procedural_{candidate_hash[:16]}"
        type_expr = _variant_type_expr(canonical["type_expr"], variant)
        metadata = {
            "activation_status": "paid",
            "supply_mode": "procedural",
            "mutation_depth": 2,
            "mutation_chain": [
                {
                    "operator": "canonicalize_mathlib_snapshot_row",
                    "input_hash": source_hash,
                    "output_hash": canonical_hash,
                },
                {
                    "operator": "epoch_bind_procedural_task",
                    "input_hash": canonical_hash,
                    "output_hash": candidate_hash,
                },
            ],
            "generation_seed": generation_seed,
            "anchor_block": anchor_block,
            "anchor_block_hash": anchor_block_hash,
            "source_pool_hash": source_pool_hash,
            "operator_bundle_hash": operator_bundle_hash,
            "canonical_hash": candidate_hash,
            "seed_variant": variant,
            "source_row_sha256": source_hash,
            "source_theorem_name": row.theorem_name,
            "source_path": row.source_path,
            "source_line": row.source_line,
            "source_topic": row.topic,
            "source_subtopic": row.subtopic,
            "erased_proof_sha256": row.proof_sha256,
            "license_state": license_state_for(row.source_license),
            "novelty_status": "passed",
            "slot_weight": _slot_weight(row.queue_depth),
        }
        out.append(
            TaskCandidate(
                id=f"lemma.procedural.depth2.{candidate_hash[:24]}",
                title=f"Procedural depth-2 {row.theorem_name}",
                source_stream="procedural",
                source_ref=SourceRef(
                    kind="procedural",
                    name=f"depth2-{candidate_hash[:16]}",
                    commit=row.mathlib_rev,
                    path=row.source_path,
                ),
                source_license=row.source_license,
                imports=row.imports,
                theorem_name=theorem_name,
                type_expr=type_expr,
                statement=f"theorem {theorem_name} : {type_expr} := by\n  sorry",
                submission_stub=lean_stub(theorem_name, type_expr, row.imports),
                mathlib_rev=row.mathlib_rev,
                queue_depth=row.queue_depth,
                metadata={key: value for key, value in metadata.items() if value is not None},
            )
        )
        if limit is not None and len(out) >= limit:
            break
    return tuple(out)


def build_epoch_tasks_from_mathlib_rows(
    rows: tuple[MathlibSnapshotRow, ...],
    *,
    generation_seed: str,
    anchor_block: int,
    anchor_block_hash: str,
    active_k: int,
    frontier_depth: int,
) -> tuple[LemmaTask, ...]:
    candidates = generate_depth2_candidates_from_mathlib_rows(
        tuple(row for row in rows if row.queue_depth <= frontier_depth),
        generation_seed=generation_seed,
        anchor_block=anchor_block,
        anchor_block_hash=anchor_block_hash,
    )
    build = build_procedural_registry_tasks(candidates, seed=generation_seed, frontier_depth=frontier_depth)
    if build.rejected:
        detail = ", ".join(f"{item.id}:{item.reason}" for item in build.rejected[:5])
        raise ValueError(f"procedural source pool rejected: {detail}")
    return build.tasks[:active_k]


def build_procedural_registry_tasks(
    candidates: tuple[TaskCandidate, ...],
    *,
    seed: str,
    frontier_depth: int | None = None,
) -> ProceduralRegistryBuild:
    tasks: list[LemmaTask] = []
    rejected: list[RejectedProceduralCandidate] = []
    for candidate in candidates:
        task = candidate.to_task(frontier_depth=frontier_depth)
        reason = production_supply_rejection_reason(task)
        if reason:
            rejected.append(RejectedProceduralCandidate(candidate.id, reason))
            continue
        tasks.append(
            task.model_copy(
                update={
                    "activation_status": "paid",
                    "triviality_status": label_from_baseline(
                        solved_by_baseline=bool(candidate.metadata.get("baseline_solved")),
                        queue_depth=candidate.queue_depth,
                    ),
                    "difficulty_band": _difficulty_band(candidate.queue_depth),
                }
            )
        )
    queued = tuple(
        task.model_copy(update={"queue_position": index})
        for index, task in enumerate(deterministic_queue(tasks, seed=seed, max_frontier_depth=frontier_depth))
    )
    return ProceduralRegistryBuild(tasks=queued, rejected=tuple(rejected))


def _row_payload(row: MathlibSnapshotRow) -> dict[str, Any]:
    return row.model_dump(mode="json", exclude_none=True)


def _canonical_row_payload(row: MathlibSnapshotRow) -> dict[str, Any]:
    return {
        "theorem_name": row.theorem_name.strip(),
        "type_expr": " ".join(row.type_expr.split()),
        "imports": tuple(row.imports),
        "mathlib_rev": row.mathlib_rev.strip(),
        "source_path": row.source_path,
        "source_line": row.source_line,
        "source_license": row.source_license,
        "proof_sha256": row.proof_sha256,
    }


def _hash_json(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _seeded_row_key(generation_seed: str, row: MathlibSnapshotRow) -> str:
    return hashlib.sha256(f"{generation_seed}:{_hash_json(_row_payload(row))}".encode()).hexdigest()


def _variant_type_expr(type_expr: str, variant: str) -> str:
    base = f"({type_expr})"
    if variant == "and_true_right":
        return f"{base} ∧ True"
    if variant == "and_true_left":
        return f"True ∧ {base}"
    if variant == "and_self":
        return f"{base} ∧ {base}"
    if variant == "or_false":
        return f"{base} ∨ False"
    raise ValueError(f"unknown seed variant: {variant}")


def _read_source_pool_bytes(source: str) -> bytes:
    src = source.strip()
    if not src:
        raise ValueError("LEMMA_TASK_SOURCE_POOL_URL is required")
    if src.startswith(("http://", "https://")):
        try:
            response = httpx.get(src, timeout=30.0, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise ValueError(f"could not fetch source pool: {e}") from e
        return response.content
    if src.startswith("file://"):
        parsed = urlparse(src)
        path = Path(unquote(parsed.path))
    else:
        path = Path(src).expanduser()
    try:
        return path.read_bytes()
    except OSError as e:
        raise ValueError(f"could not read source pool {path}: {e}") from e


def _normalize_sha256(value: str | None) -> str | None:
    raw = (value or "").strip().lower()
    if raw.startswith("sha256:"):
        raw = raw.removeprefix("sha256:")
    return raw or None


def _slot_weight(queue_depth: int) -> float:
    return float(1 + max(0, queue_depth))


def _difficulty_band(queue_depth: int) -> str:
    if queue_depth <= 1:
        return "easy"
    if queue_depth <= 3:
        return "medium"
    if queue_depth <= 6:
        return "hard"
    return "frontier"
