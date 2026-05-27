"""Production procedural task-supply generation and registry building."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lemma.license import license_state_for, paid_license_allowed
from lemma.protocol_invariants import procedural_gate_receipt_sha256, production_supply_rejection_reason
from lemma.supply.gates import GATE_VERSION, AssumedProceduralGateRunner, ProceduralGateRunner, ProceduralGateVerdict
from lemma.supply.mutation import PreviewMutationEngine, ProceduralMutationEngine
from lemma.supply.novelty import statement_hash
from lemma.supply.operator_bundle import (
    OPERATOR_BUNDLE_VERSION,
    SMALL_VALUES_BY_TYPE,
    TYPE_SUBSTITUTIONS,
    procedural_operator_bundle_hash,
)
from lemma.supply.source_pool import source_pool_receipt, source_pool_receipt_sha256
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


@dataclass(frozen=True)
class _Depth2GenerationContext:
    ordered: tuple[TaskCandidate, ...]
    generation_seed: str
    epoch_fields: dict[str, Any]
    pool_hash: str
    pool_receipt: dict[str, object]
    operator_hash: str
    tempo: int
    mutation_engine: ProceduralMutationEngine
    gate_runner: ProceduralGateRunner


@dataclass(frozen=True)
class _Depth2Attempt:
    cursor: int
    candidate: TaskCandidate | None
    verdict: ProceduralGateVerdict | None


_SAFE_IDENT = re.compile(r"[^A-Za-z0-9_]+")
_LOW_VALUE_MUTATION_FALLBACKS = frozenset(
    {
        "true_premise",
        "unsupported_binder_type",
        "no_supported_type_occurrence",
    }
)
_LOW_VALUE_MUTATION_MODES = frozenset({"peer_premise"})
_LOW_VALUE_MUTATION_RULES = frozenset({"conjoin_peer_conclusion", "false_disjunct"})
_LOW_VALUE_MUTATION_TARGETS = frozenset({"fresh_prop_hypothesis"})
_PRODUCTIVE_OPERATOR_NAMES = ("substitute-type",)


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


def corpus_sources_from_dir(
    corpus_dir: Path,
    *,
    before_tempo: int | None = None,
    citation_window_tempos: int = 2000,
) -> tuple[TaskCandidate, ...]:
    """Load prior accepted corpus rows as mutation sources."""
    from lemma.corpus import CorpusRow

    if not corpus_dir.is_dir():
        return ()
    rows: list[CorpusRow] = []
    seen_row_ids: set[str] = set()
    for path in _corpus_row_paths(corpus_dir):
        if path.suffix == ".json":
            try:
                row = CorpusRow.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, ValueError) as e:
                raise ValueError(f"{path}: invalid corpus row source: {e}") from e
            if _prior_row_usable(row, before_tempo=before_tempo, seen_row_ids=seen_row_ids):
                rows.append(row)
            continue
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = CorpusRow.model_validate(json.loads(line))
            except (json.JSONDecodeError, ValueError) as e:
                raise ValueError(f"{path}:{no}: invalid corpus row source: {e}") from e
            if _prior_row_usable(row, before_tempo=before_tempo, seen_row_ids=seen_row_ids):
                rows.append(row)
    citation_counts = _substrate_citation_counts(
        rows,
        before_tempo=before_tempo,
        citation_window_tempos=citation_window_tempos,
    )
    sources: list[TaskCandidate] = []
    for row in rows:
        task = row.to_task()
        license_state = license_state_for(row.source_license, str(row.metadata.get("license_state") or ""))
        sources.append(
            TaskCandidate(
                id=f"lemma.substrate.{row.row_id[:16]}",
                title=f"Prior Lemma {row.theorem_name}",
                source_stream="lemma_substrate",
                source_ref=SourceRef(
                    kind="lemma_substrate",
                    name=row.row_id,
                    commit=row.proof_identity,
                    path=f"tempo-{row.tempo}/accepted/{row.row_id}.json",
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
                    "citation_weight": citation_counts.get(row.row_id, 0),
                    "citation_window_tempos": max(1, int(citation_window_tempos)),
                    "direct_dependency_count": row.dependencies.direct_dependency_count,
                    "dependency_depth": row.dependencies.dependency_depth,
                    "transitive_dependency_hash": row.dependencies.transitive_dependency_hash,
                    "lemma_rows_used": (row.row_id,),
                    "license_state": license_state,
                    "substrate_row_id": row.row_id,
                    "substrate_tempo": row.tempo,
                    "proof_identity_strength": row.proof_identity_strength,
                },
            )
        )
    return tuple(sources)


def _corpus_row_paths(corpus_dir: Path) -> tuple[Path, ...]:
    raw_epoch_files = sorted(corpus_dir.glob("epoch-*.jsonl"))
    canonical_entries = sorted(corpus_dir.glob("**/tempos/tempo-*/entries/*.json"))
    return tuple([*raw_epoch_files, *canonical_entries])


def _prior_row_usable(
    row: Any,
    *,
    before_tempo: int | None,
    seen_row_ids: set[str],
) -> bool:
    if before_tempo is not None and (row.tempo is None or row.tempo >= before_tempo):
        return False
    if row.row_id in seen_row_ids:
        return False
    if not _usable_substrate_row(row):
        return False
    seen_row_ids.add(row.row_id)
    return True


def _substrate_citation_counts(
    rows: list[Any],
    *,
    before_tempo: int | None,
    citation_window_tempos: int,
) -> dict[str, int]:
    lower_bound = None if before_tempo is None else max(0, before_tempo - max(1, int(citation_window_tempos)))
    counts: dict[str, int] = {}
    for row in rows:
        if lower_bound is not None and (row.tempo is None or row.tempo < lower_bound):
            continue
        for cited in row.dependencies.lemma_rows_used:
            counts[str(cited)] = counts.get(str(cited), 0) + 1
    return counts


def _usable_substrate_row(row: Any) -> bool:
    if not row.rewarded or not row.full_reward_eligible:
        return False
    if row.proof_identity_strength != "strong":
        return False
    if not row.verification.passed:
        return False
    return paid_license_allowed(license_state_for(row.source_license, str(row.metadata.get("license_state") or "")))


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
            "citation_weight": _citation_weight_for_hash(source),
            "citation_window_tempos": _metadata_int(source.metadata.get("citation_window_tempos")),
            "direct_dependency_count": _metadata_int(source.metadata.get("direct_dependency_count")),
            "dependency_depth": _metadata_int(source.metadata.get("dependency_depth")),
            "transitive_dependency_hash": str(source.metadata.get("transitive_dependency_hash") or ""),
        }
        for source in sorted(sources, key=lambda item: item.id)
    ]
    return _hash_json({"version": "lemma-source-pool-v1", "sources": payload})


def _resolve_generation_workers(generation_workers: int | None) -> int:
    configured = generation_workers
    if configured is None or configured <= 0:
        raw = os.environ.get("LEMMA_PROCEDURAL_GENERATION_WORKERS", "").strip()
        if raw.isdigit() and int(raw) > 0:
            configured = int(raw)
    if configured is None or configured <= 0:
        return min(8, max(1, os.cpu_count() or 1))
    return max(1, configured)


def generate_depth2_candidates(
    sources: tuple[TaskCandidate, ...],
    *,
    generation_seed: str,
    epoch_randomness: str,
    count: int,
    tempo: int,
    max_queue_depth: int | None = None,
    citation_alpha: float = 0.5,
    citation_weight_cap: float = 64.0,
    citation_window_tempos: int = 2000,
    gate_runner: ProceduralGateRunner | None = None,
    mutation_engine: ProceduralMutationEngine | None = None,
    generation_workers: int | None = None,
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
    eligible_sources = _eligible_depth2_sources(sources, max_queue_depth=max_queue_depth)
    if not eligible_sources:
        raise ValueError(f"source pool has no candidates at max_queue_depth={max_queue_depth}")

    pool_hash = source_pool_hash(sources)
    pool_receipt = source_pool_receipt(
        sources,
        source_pool_sha256=pool_hash,
        citation_alpha=citation_alpha,
        citation_weight_cap=citation_weight_cap,
        citation_window_tempos=citation_window_tempos,
    )
    operator_hash = procedural_operator_bundle_hash()
    epoch_fields = _epoch_fields(epoch_randomness)
    ordered = _ordered_sources(
        eligible_sources,
        seed=generation_seed,
        citation_alpha=citation_alpha,
        citation_weight_cap=citation_weight_cap,
    )
    ctx = _Depth2GenerationContext(
        ordered=ordered,
        generation_seed=generation_seed,
        epoch_fields=epoch_fields,
        pool_hash=pool_hash,
        pool_receipt=pool_receipt,
        operator_hash=operator_hash,
        tempo=tempo,
        mutation_engine=mutation_engine or PreviewMutationEngine(),
        gate_runner=gate_runner or AssumedProceduralGateRunner(),
    )
    workers = _resolve_generation_workers(generation_workers)
    attempt_limit = count * 50
    if workers <= 1:
        return _generate_depth2_candidates_sequential(ctx, count=count, attempt_limit=attempt_limit)
    return _generate_depth2_candidates_parallel(ctx, count=count, attempt_limit=attempt_limit, workers=workers)


def _generate_depth2_candidates_sequential(
    ctx: _Depth2GenerationContext,
    *,
    count: int,
    attempt_limit: int,
) -> tuple[TaskCandidate, ...]:
    out: list[TaskCandidate] = []
    seen: set[str] = set()
    cursor = 0
    while len(out) < count and cursor < attempt_limit:
        attempt = _attempt_depth2_candidate(ctx, cursor=cursor, seen_canonical_hashes=frozenset(seen))
        if attempt.candidate is not None and attempt.verdict is not None:
            _maybe_accept_depth2_attempt(out, seen, attempt.candidate, attempt.verdict)
        cursor += 1
    if len(out) < count:
        raise ValueError(f"procedural gates accepted {len(out)} candidates, needed {count}")
    return tuple(out)


def _generate_depth2_candidates_parallel(
    ctx: _Depth2GenerationContext,
    *,
    count: int,
    attempt_limit: int,
    workers: int,
) -> tuple[TaskCandidate, ...]:
    out: list[TaskCandidate] = []
    seen: set[str] = set()
    cursor = 0
    while len(out) < count and cursor < attempt_limit:
        batch_end = min(cursor + workers, attempt_limit)
        seen_snapshot = frozenset(seen)
        batch_cursors = range(cursor, batch_end)
        attempts = _attempt_depth2_candidates_parallel(ctx, batch_cursors, seen_snapshot, workers=workers)
        for attempt in sorted(attempts, key=lambda item: item.cursor):
            if len(out) >= count:
                break
            if attempt.candidate is None or attempt.verdict is None:
                continue
            _maybe_accept_depth2_attempt(out, seen, attempt.candidate, attempt.verdict)
        cursor = batch_end
    if len(out) < count:
        raise ValueError(f"procedural gates accepted {len(out)} candidates, needed {count}")
    return tuple(out)


def _attempt_depth2_candidates_parallel(
    ctx: _Depth2GenerationContext,
    cursors: range,
    seen_snapshot: frozenset[str],
    *,
    workers: int,
) -> tuple[_Depth2Attempt, ...]:
    if not cursors:
        return ()
    worker_count = min(workers, len(cursors))
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {
            pool.submit(_attempt_depth2_candidate, ctx, cursor=cursor, seen_canonical_hashes=seen_snapshot): cursor
            for cursor in cursors
        }
        attempts: list[_Depth2Attempt] = []
        for future in as_completed(futures):
            attempts.append(future.result())
    return tuple(attempts)


def _attempt_depth2_candidate(
    ctx: _Depth2GenerationContext,
    *,
    cursor: int,
    seen_canonical_hashes: frozenset[str],
) -> _Depth2Attempt:
    source = ctx.ordered[cursor % len(ctx.ordered)]
    try:
        candidate = _candidate_from_source(
            source,
            source_pool=ctx.ordered,
            generation_seed=ctx.generation_seed,
            epoch_fields=ctx.epoch_fields,
            mutation_engine=ctx.mutation_engine,
            source_pool_hash_value=ctx.pool_hash,
            source_pool_receipt_value=ctx.pool_receipt,
            operator_bundle_hash=ctx.operator_hash,
            tempo=ctx.tempo,
            sequence=cursor,
        )
    except ValueError:
        return _Depth2Attempt(cursor=cursor, candidate=None, verdict=None)
    verdict = ctx.gate_runner(candidate, seen_canonical_hashes=seen_canonical_hashes)
    return _Depth2Attempt(cursor=cursor, candidate=candidate, verdict=verdict)


def _maybe_accept_depth2_attempt(
    out: list[TaskCandidate],
    seen: set[str],
    candidate: TaskCandidate,
    verdict: ProceduralGateVerdict,
) -> None:
    candidate = _with_gate_receipt(candidate, verdict)
    canonical_hash = str(candidate.metadata["canonical_hash"])
    if not verdict.accepted or canonical_hash in seen:
        return
    seen.add(canonical_hash)
    out.append(candidate)


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
    source_pool: tuple[TaskCandidate, ...],
    generation_seed: str,
    epoch_fields: dict[str, Any],
    mutation_engine: ProceduralMutationEngine,
    source_pool_hash_value: str,
    source_pool_receipt_value: dict[str, object],
    operator_bundle_hash: str,
    tempo: int,
    sequence: int,
    operator_chain: tuple[str, ...] | None = None,
) -> TaskCandidate:
    type_expr = source.type_expr.strip()
    imports = source.imports
    mutation_chain: list[dict[str, object]] = []
    input_hash = _hash_text(type_expr)
    for step in range(2):
        operator = (
            operator_chain[step]
            if operator_chain is not None and step < len(operator_chain)
            else _operator_for_step(generation_seed, sequence, step, type_expr)
        )
        if operator is None:
            raise ValueError("no productive procedural operator for current type")
        peer = _peer_source(source_pool, source_id=source.id, seed=generation_seed, sequence=sequence, step=step)
        imports = _combined_imports(imports, peer.imports)
        mutation = mutation_engine.apply(
            source,
            type_expr,
            operator,
            step=step,
            param_seed=_hash_text(f"{generation_seed}:{sequence}:{step}:{operator}"),
            peer=peer,
        )
        _reject_low_value_mutation(mutation.params)
        output_hash = _hash_text(mutation.type_expr)
        mutation_chain.append(
            {
                "operator": operator,
                "params": mutation.params,
                "input_hash": input_hash,
                "output_hash": output_hash,
            }
        )
        type_expr = mutation.type_expr
        input_hash = output_hash

    if all(step["operator"] == "specialize" for step in mutation_chain):
        raise ValueError("low-value procedural mutation chain: specialize_only")

    canonical_hash = _hash_json(
        {
            "source_id": source.id,
            "type_expr": type_expr,
            "mutation_chain": mutation_chain,
            "generation_seed": generation_seed,
        }
    )
    mutated_statement_hash = statement_hash(type_expr)
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
        "tempo": tempo,
        "mutation_depth": 2,
        "mutation_chain": mutation_chain,
        "generation_seed": generation_seed,
        "drand_round": _nonnegative_int(epoch_fields.get("drand_round")),
        "anchor_block": _nonnegative_int(epoch_fields.get("anchor_block")),
        "source_pool_hash": source_pool_hash_value,
        "source_pool_receipt_version": source_pool_receipt_value["version"],
        "source_pool_receipt_sha256": source_pool_receipt_sha256(source_pool_receipt_value),
        "source_pool_source_count": source_pool_receipt_value["source_count"],
        "source_pool_stream_counts": source_pool_receipt_value["source_stream_counts"],
        "source_sampling_version": source_pool_receipt_value["sampling_version"],
        "citation_alpha_basis_points": source_pool_receipt_value["citation_alpha_basis_points"],
        "citation_weight_cap_micros": source_pool_receipt_value["citation_weight_cap_micros"],
        "citation_window_tempos": source_pool_receipt_value["citation_window_tempos"],
        "operator_bundle_version": OPERATOR_BUNDLE_VERSION,
        "operator_bundle_hash": operator_bundle_hash,
        "canonical_hash": canonical_hash,
        "statement_hash": mutated_statement_hash,
        "license_state": license_state_for(source.source_license, str(source.metadata.get("license_state") or "")),
        "source_task_id": source.id,
        "source_theorem_name": source.theorem_name,
        "source_target_sha256": _hash_text(source.statement),
    }
    if "tempo_length" in epoch_fields:
        metadata["active_window_blocks"] = _nonnegative_int(epoch_fields.get("tempo_length"))
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
        imports=imports,
        theorem_name=theorem_name,
        type_expr=type_expr,
        statement=f"theorem {theorem_name} : {type_expr} := by\n  sorry",
        submission_stub=_lean_stub(theorem_name, type_expr, imports),
        lean_toolchain=source.lean_toolchain,
        mathlib_rev=source.mathlib_rev,
        policy=source.policy,
        queue_depth=source.queue_depth,
        metadata=metadata,
    )


def _reject_low_value_mutation(params: dict[str, object]) -> None:
    fallback = params.get("fallback")
    mode = params.get("mode")
    rule = params.get("rule")
    target = params.get("target")
    if fallback in _LOW_VALUE_MUTATION_FALLBACKS:
        raise ValueError(f"low-value procedural mutation fallback: {fallback}")
    if mode in _LOW_VALUE_MUTATION_MODES:
        raise ValueError(f"low-value procedural mutation mode: {mode}")
    if rule in _LOW_VALUE_MUTATION_RULES:
        raise ValueError(f"low-value procedural mutation rule: {rule}")
    if target in _LOW_VALUE_MUTATION_TARGETS:
        raise ValueError(f"low-value procedural mutation target: {target}")


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


def _operator_chain(seed: str, sequence: int, type_expr: str) -> tuple[str, ...]:
    operators = _productive_operators_for(type_expr)
    if not operators:
        return ()
    return (
        operators[_hash_int(f"{seed}:{sequence}:0") % len(operators)],
        operators[_hash_int(f"{seed}:{sequence}:1") % len(operators)],
    )


def _operator_for_step(seed: str, sequence: int, step: int, type_expr: str) -> str | None:
    operators = _productive_operators_for(type_expr)
    if not operators:
        return None
    return operators[_hash_int(f"{seed}:{sequence}:{step}") % len(operators)]


def _eligible_depth2_sources(
    sources: tuple[TaskCandidate, ...],
    *,
    max_queue_depth: int | None,
) -> tuple[TaskCandidate, ...]:
    return tuple(
        source
        for source in sources
        if (max_queue_depth is None or source.queue_depth <= max_queue_depth)
        and _productive_operators_for(source.type_expr)
    )


def _productive_operators_for(type_expr: str) -> tuple[str, ...]:
    out: list[str] = []
    if _has_small_specialization_target(type_expr):
        out.append("specialize")
    if any(_lean_token_present(type_expr, source) for source, _replacement in TYPE_SUBSTITUTIONS):
        out.append("substitute-type")
    if _has_top_level_arrow(type_expr):
        out.append("weaken")
    return tuple(operator for operator in _PRODUCTIVE_OPERATOR_NAMES if operator in out)


def _has_small_specialization_target(type_expr: str) -> bool:
    return "∀ " in type_expr and any(
        _lean_token_present(type_expr, binder_type)
        for binder_type in SMALL_VALUES_BY_TYPE
        if binder_type != "Prop"
    )


def _lean_token_present(value: str, token: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_'.]){re.escape(token)}(?![A-Za-z0-9_'.])", value) is not None


def _has_top_level_arrow(value: str) -> bool:
    return _top_level_index(value, "→") is not None or _top_level_index(value, "->") is not None


def _top_level_index(value: str, marker: str) -> int | None:
    depth = 0
    i = 0
    while i < len(value):
        char = value[i]
        if char in "([{":
            depth += 1
        elif char in ")]}" and depth > 0:
            depth -= 1
        elif depth == 0 and value.startswith(marker, i):
            return i
        i += 1
    return None


def _peer_source(
    sources: tuple[TaskCandidate, ...],
    *,
    source_id: str,
    seed: str,
    sequence: int,
    step: int,
) -> TaskCandidate:
    peers = tuple(source for source in sources if source.id != source_id) or sources
    return peers[_hash_int(f"{seed}:{sequence}:{step}:peer") % len(peers)]


def _combined_imports(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    for item in (*left, *right):
        if item not in out:
            out.append(item)
    return tuple(out)


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
    raw_weight = _metadata_float(source.metadata.get("citation_weight"))
    weight = min(cap, raw_weight if raw_weight is not None else 1.0)
    if weight <= 0:
        return math.inf, source.id
    return -math.log(_unit_interval(f"{seed}:weighted:{source.id}:{source.type_expr}")) / weight, source.id


def _citation_weight_for_hash(source: TaskCandidate) -> float:
    value = _metadata_float(source.metadata.get("citation_weight"))
    return 1.0 if value is None else value


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
