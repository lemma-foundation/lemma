"""Production procedural task-supply generation and registry building."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lemma.license import license_state_for
from lemma.protocol_invariants import procedural_gate_receipt_sha256, production_supply_rejection_reason
from lemma.supply.gates import GATE_VERSION, AssumedProceduralGateRunner, ProceduralGateRunner, ProceduralGateVerdict
from lemma.supply.types import TaskCandidate
from lemma.task_supply import deterministic_queue
from lemma.tasks import LemmaTask, SourceRef


@dataclass(frozen=True)
class RejectedProceduralCandidate:
    id: str
    reason: str


@dataclass(frozen=True)
class ProceduralRegistryBuild:
    tasks: tuple[LemmaTask, ...]
    rejected: tuple[RejectedProceduralCandidate, ...]


OPERATOR_BUNDLE_VERSION = "lemma-procedural-depth2-v1"
OPERATOR_NAMES = ("specialize", "generalize", "substitute-type")
_SAFE_IDENT = re.compile(r"[^A-Za-z0-9_]+")


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


def corpus_sources_from_dir(corpus_dir: Path, *, before_tempo: int | None = None) -> tuple[TaskCandidate, ...]:
    """Load prior accepted corpus rows as mutation sources."""
    from lemma.corpus import CorpusRow

    if not corpus_dir.is_dir():
        return ()
    sources: list[TaskCandidate] = []
    for path in sorted(corpus_dir.glob("epoch-*.jsonl")):
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = CorpusRow.model_validate(json.loads(line))
            except (json.JSONDecodeError, ValueError) as e:
                raise ValueError(f"{path}:{no}: invalid corpus row source: {e}") from e
            if before_tempo is not None and row.tempo is not None and row.tempo >= before_tempo:
                continue
            task = row.to_task()
            sources.append(
                TaskCandidate(
                    id=f"lemma.substrate.{row.row_id[:16]}",
                    title=f"Prior Lemma {row.theorem_name}",
                    source_stream="lemma_substrate",
                    source_ref=SourceRef(
                        kind="lemma_substrate",
                        name=row.row_id,
                        commit=row.proof_identity,
                        path=path.name,
                    ),
                    source_license=row.source_license,
                    imports=task.imports,
                    theorem_name=task.theorem_name,
                    type_expr=task.type_expr,
                    statement=task.statement,
                    submission_stub=task.submission_stub,
                    lean_toolchain=task.lean_toolchain,
                    mathlib_rev=task.mathlib_rev,
                    policy=task.policy,
                    queue_depth=task.queue_depth,
                    metadata={
                        "citation_weight": row.dependencies.dependency_depth or 1,
                        "direct_dependency_count": row.dependencies.direct_dependency_count,
                        "dependency_depth": row.dependencies.dependency_depth,
                        "transitive_dependency_hash": row.dependencies.transitive_dependency_hash,
                        "lemma_rows_used": (row.row_id,),
                        "license_state": license_state_for(
                            row.source_license,
                            str(row.metadata.get("license_state") or ""),
                        ),
                        "substrate_row_id": row.row_id,
                    },
                )
            )
    return tuple(sources)


def procedural_operator_bundle_hash() -> str:
    payload = {
        "version": OPERATOR_BUNDLE_VERSION,
        "operators": OPERATOR_NAMES,
        "chain_depth": 2,
    }
    return _hash_json(payload)


def source_pool_hash(sources: tuple[TaskCandidate, ...]) -> str:
    payload = [
        {
            "id": source.id,
            "source_stream": source.source_stream,
            "source_ref": source.source_ref.model_dump(mode="json", exclude_none=True),
            "source_license": source.source_license,
            "imports": source.imports,
            "theorem_name": source.theorem_name,
            "type_expr": source.type_expr,
            "lean_toolchain": source.lean_toolchain,
            "mathlib_rev": source.mathlib_rev,
            "queue_depth": source.queue_depth,
            "citation_weight": _metadata_float(source.metadata.get("citation_weight")) or 1.0,
            "direct_dependency_count": _metadata_int(source.metadata.get("direct_dependency_count")),
            "dependency_depth": _metadata_int(source.metadata.get("dependency_depth")),
            "transitive_dependency_hash": str(source.metadata.get("transitive_dependency_hash") or ""),
        }
        for source in sorted(sources, key=lambda item: item.id)
    ]
    return _hash_json({"version": "lemma-source-pool-v1", "sources": payload})


def generate_depth2_candidates(
    sources: tuple[TaskCandidate, ...],
    *,
    generation_seed: str,
    epoch_randomness: str,
    count: int,
    tempo: int,
    citation_alpha: float = 0.25,
    citation_weight_cap: float = 100.0,
    gate_runner: ProceduralGateRunner | None = None,
) -> tuple[TaskCandidate, ...]:
    """Generate fresh procedural candidates from public source rows.

    This is the protocol-shaped supply path: the task rows are derived from
    public source metadata plus the epoch seed, not chosen from a static
    playlist. The generated rows still flow through the existing registry
    builder so the paid-production gates stay centralized in one place.
    """
    if count < 1:
        raise ValueError("count must be positive")
    if not sources:
        raise ValueError("source pool must not be empty")

    pool_hash = source_pool_hash(sources)
    operator_hash = procedural_operator_bundle_hash()
    epoch_fields = _epoch_fields(epoch_randomness)
    ordered = _ordered_sources(
        sources,
        seed=generation_seed,
        citation_alpha=citation_alpha,
        citation_weight_cap=citation_weight_cap,
    )
    runner = gate_runner or AssumedProceduralGateRunner()
    out: list[TaskCandidate] = []
    seen: set[str] = set()
    cursor = 0
    attempt_limit = max(count * 50, len(ordered) * 20)
    while len(out) < count and cursor < attempt_limit:
        source = ordered[cursor % len(ordered)]
        chain = _operator_chain(generation_seed, cursor)
        candidate = _candidate_from_source(
            source,
            generation_seed=generation_seed,
            epoch_fields=epoch_fields,
            operator_chain=chain,
            source_pool_hash_value=pool_hash,
            operator_bundle_hash=operator_hash,
            tempo=tempo,
            sequence=cursor,
        )
        verdict = runner(candidate, seen_canonical_hashes=seen)
        candidate = _with_gate_receipt(candidate, verdict)
        canonical_hash = str(candidate.metadata["canonical_hash"])
        if verdict.accepted:
            seen.add(canonical_hash)
            out.append(candidate)
        cursor += 1
    if len(out) < count:
        raise ValueError(f"procedural gates accepted {len(out)} candidates, needed {count}")
    return tuple(out)


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
                    "triviality_status": _triviality_status(task.queue_depth),
                    "difficulty_band": _difficulty_band(task.queue_depth),
                    "metadata": {
                        **task.metadata,
                        "activation_status": "paid",
                        "license_state": license_state_for(
                            task.source_license,
                            str(task.metadata.get("license_state") or ""),
                        ),
                    },
                }
            )
        )
    queued = tuple(
        task.model_copy(update={"queue_position": index})
        for index, task in enumerate(deterministic_queue(tasks, seed=seed, max_frontier_depth=frontier_depth))
    )
    return ProceduralRegistryBuild(tasks=queued, rejected=tuple(rejected))


def _candidate_from_source(
    source: TaskCandidate,
    *,
    generation_seed: str,
    epoch_fields: dict[str, Any],
    operator_chain: tuple[str, str],
    source_pool_hash_value: str,
    operator_bundle_hash: str,
    tempo: int,
    sequence: int,
) -> TaskCandidate:
    type_expr = source.type_expr.strip()
    mutation_chain: list[dict[str, object]] = []
    input_hash = _hash_text(type_expr)
    for step, operator in enumerate(operator_chain):
        output_expr = _apply_operator(type_expr, operator, step=step)
        output_hash = _hash_text(output_expr)
        mutation_chain.append(
            {
                "operator": operator,
                "input_hash": input_hash,
                "output_hash": output_hash,
            }
        )
        type_expr = output_expr
        input_hash = output_hash

    canonical_hash = _hash_json(
        {
            "source_id": source.id,
            "type_expr": type_expr,
            "mutation_chain": mutation_chain,
            "generation_seed": generation_seed,
        }
    )
    theorem_name = _theorem_name(source.theorem_name, canonical_hash)
    source_ref = SourceRef(
        kind="procedural",
        name=f"tempo-{tempo}-seq-{sequence}",
        commit=source.mathlib_rev,
        path=source.source_ref.path,
    )
    metadata = {
        "activation_status": "paid",
        "supply_mode": "procedural",
        "mutation_depth": 2,
        "mutation_chain": mutation_chain,
        "generation_seed": generation_seed,
        "drand_round": _nonnegative_int(epoch_fields.get("drand_round")),
        "anchor_block": _nonnegative_int(epoch_fields.get("anchor_block")),
        "source_pool_hash": source_pool_hash_value,
        "operator_bundle_hash": operator_bundle_hash,
        "canonical_hash": canonical_hash,
        "license_state": license_state_for(source.source_license, str(source.metadata.get("license_state") or "")),
        "source_task_id": source.id,
        "source_theorem_name": source.theorem_name,
        "source_target_sha256": _hash_text(source.statement),
    }
    for key in (
        "citation_weight",
        "direct_dependency_count",
        "dependency_depth",
        "transitive_dependency_hash",
        "lemma_rows_used",
        "substrate_row_id",
    ):
        if key in source.metadata:
            metadata[key] = source.metadata[key]
    return TaskCandidate(
        id=f"lemma.procedural.{canonical_hash[:16]}",
        title=f"Procedural {source.title or source.theorem_name}",
        source_stream="procedural",
        source_ref=source_ref,
        source_license=source.source_license,
        imports=source.imports,
        theorem_name=theorem_name,
        type_expr=type_expr,
        statement=f"theorem {theorem_name} : {type_expr} := by\n  sorry",
        submission_stub=_lean_stub(theorem_name, type_expr, source.imports),
        lean_toolchain=source.lean_toolchain,
        mathlib_rev=source.mathlib_rev,
        policy=source.policy,
        queue_depth=source.queue_depth,
        metadata=metadata,
    )


def _with_gate_receipt(candidate: TaskCandidate, verdict: ProceduralGateVerdict) -> TaskCandidate:
    metadata = {
        **candidate.metadata,
        **verdict.metadata,
        "typechecked": verdict.typechecked,
        "prop_gate_passed": verdict.prop_gate_passed,
        "triviality_checked": verdict.triviality_checked,
        "baseline_solved": verdict.baseline_solved,
        "novelty_status": verdict.novelty_status,
        "slot_weight": verdict.slot_weight,
        "gate_version": GATE_VERSION,
    }
    task = candidate.model_copy(update={"metadata": metadata}).to_task()
    metadata = {**metadata, "gate_receipt_sha256": procedural_gate_receipt_sha256(task)}
    return candidate.model_copy(update={"metadata": metadata})


def _apply_operator(type_expr: str, operator: str, *, step: int) -> str:
    expr = type_expr.strip()
    if operator == "specialize":
        return f"({expr}) ∧ True"
    if operator == "generalize":
        return f"∀ lemma_p{step} : Prop, lemma_p{step} → ({expr})"
    if operator == "substitute-type":
        return f"True → ({expr})"
    raise ValueError(f"unknown procedural operator: {operator}")


def _operator_chain(seed: str, sequence: int) -> tuple[str, str]:
    return (
        OPERATOR_NAMES[_hash_int(f"{seed}:{sequence}:0") % len(OPERATOR_NAMES)],
        OPERATOR_NAMES[_hash_int(f"{seed}:{sequence}:1") % len(OPERATOR_NAMES)],
    )


def _ordered_sources(
    sources: tuple[TaskCandidate, ...],
    *,
    seed: str,
    citation_alpha: float,
    citation_weight_cap: float,
) -> tuple[TaskCandidate, ...]:
    alpha = min(1.0, max(0.0, float(citation_alpha)))
    cap = max(1.0, float(citation_weight_cap))
    uniform = sorted(sources, key=lambda source: _hash_text(f"{seed}:uniform:{source.id}:{source.type_expr}"))
    weighted = sorted(sources, key=lambda source: _weighted_source_key(source, seed=seed, cap=cap))
    used: set[str] = set()
    out: list[TaskCandidate] = []
    lanes = {"uniform": iter(uniform), "weighted": iter(weighted)}
    while len(out) < len(sources):
        lane = "weighted" if _unit_interval(f"{seed}:lane:{len(out)}") < alpha else "uniform"
        fallback_lane = "weighted" if lane == "uniform" else "uniform"
        source = _next_unused(lanes[lane], used) or _next_unused(lanes[fallback_lane], used)
        if source is None:
            break
        used.add(source.id)
        out.append(source)
    return tuple(out)


def _next_unused(candidates: Iterator[TaskCandidate], used: set[str]) -> TaskCandidate | None:
    for source in candidates:
        if source.id not in used:
            return source
    return None


def _weighted_source_key(source: TaskCandidate, *, seed: str, cap: float) -> tuple[float, str]:
    weight = min(cap, max(1.0, _metadata_float(source.metadata.get("citation_weight")) or 1.0))
    return -math.log(_unit_interval(f"{seed}:weighted:{source.id}:{source.type_expr}")) / weight, source.id


def _unit_interval(value: str) -> float:
    return max(1, _hash_int(value)) / float(2**256)


def _metadata_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        out = float(value)
        return out if math.isfinite(out) else None
    if isinstance(value, str):
        try:
            out = float(value)
        except ValueError:
            return None
        return out if math.isfinite(out) else None
    return None


def _metadata_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _epoch_fields(epoch_randomness: str) -> dict[str, Any]:
    try:
        fields = json.loads(epoch_randomness)
    except json.JSONDecodeError:
        fields = {}
    return fields if isinstance(fields, dict) else {}


def _nonnegative_int(value: object) -> int:
    if isinstance(value, int) and value >= 0:
        return value
    return 0


def _hash_json(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(canonical).hexdigest()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_int(value: str) -> int:
    return int(_hash_text(value), 16)


def _theorem_name(source_name: str, canonical_hash: str) -> str:
    stem = _SAFE_IDENT.sub("_", source_name.replace(".", "_")).strip("_")
    if not stem or stem[0].isdigit():
        stem = f"lemma_{stem}"
    return f"procedural_{stem}_{canonical_hash[:12]}"


def _lean_stub(theorem_name: str, type_expr: str, imports: tuple[str, ...]) -> str:
    return "\n".join(
        [
            *(f"import {module}" for module in imports),
            "",
            "namespace Submission",
            "",
            f"theorem {theorem_name} : {type_expr} := by",
            "  sorry",
            "",
            "end Submission",
            "",
        ]
    )


def _triviality_status(queue_depth: int) -> str:
    if queue_depth <= 1:
        return "paid_easy"
    if queue_depth <= 3:
        return "paid_medium"
    return "paid_frontier"


def _difficulty_band(queue_depth: int) -> str:
    if queue_depth <= 1:
        return "easy"
    if queue_depth <= 3:
        return "medium"
    if queue_depth <= 6:
        return "hard"
    return "frontier"
