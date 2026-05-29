"""Production procedural task-supply generation and registry building."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from lemma.license import license_state_for, paid_license_allowed
from lemma.protocol_invariants import procedural_gate_receipt_sha256, production_supply_rejection_reason
from lemma.supply.gates import GATE_VERSION, AssumedProceduralGateRunner, ProceduralGateRunner, ProceduralGateVerdict
from lemma.supply.import_graph import ImportGraph
from lemma.supply.mutation import PreviewMutationEngine, ProceduralMutationEngine
from lemma.supply.novelty import statement_hash
from lemma.supply.operator_bundle import (
    MUTATION_ENGINE,
    OPERATOR_BUNDLE_VERSION,
    OPERATOR_NAMES,
    SMALL_VALUES_BY_TYPE,
    procedural_operator_bundle_hash,
)
from lemma.supply.source_pool import source_pool_receipt, source_pool_receipt_sha256
from lemma.supply.source_pricing import TaskPool, parse_task_pool, source_import_status, source_pricing_metadata
from lemma.supply.types import TaskCandidate
from lemma.task_supply import depth_spread_order, deterministic_queue
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
    yield_history_metadata: dict[str, object]
    operator_hash: str
    tempo: int
    import_graph: ImportGraph | None
    mutation_engine: ProceduralMutationEngine
    gate_runner: ProceduralGateRunner
    require_serious_candidates: bool


@dataclass(frozen=True)
class _Depth2Attempt:
    cursor: int
    candidate: TaskCandidate | None
    verdict: ProceduralGateVerdict | None
    rejection_reason: str | None = None


@dataclass
class _Depth2Telemetry:
    attempts: int = 0
    accepted: int = 0
    rejected: Counter[str] = field(default_factory=Counter)
    operator_chains: Counter[str] = field(default_factory=Counter)
    accepted_operator_chains: Counter[str] = field(default_factory=Counter)
    source_families: Counter[str] = field(default_factory=Counter)
    accepted_source_families: Counter[str] = field(default_factory=Counter)


@dataclass(frozen=True)
class ProceduralYieldHistory:
    sha256: str
    entries: int
    accepted_source_families: dict[str, int]
    accepted_operator_chains: dict[str, int]

    def metadata(self) -> dict[str, object]:
        return {
            "yield_history_version": YIELD_HISTORY_VERSION,
            "yield_history_sha256": self.sha256,
            "yield_history_entries": self.entries,
        }


_SAFE_IDENT = re.compile(r"[^A-Za-z0-9_]+")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_LEAN_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_GENERATED_PROP_BINDER = re.compile(
    r"^∀\s+(lemma_p\d+_[A-Za-z0-9_]+)\s*:\s*Prop,\s*\1\s*→\s*\((.*)\)$"
)
YIELD_HISTORY_VERSION = "lemma-procedural-yield-history-v1"
_LOW_VALUE_MUTATION_FALLBACKS = frozenset(
    {
        "true_premise",
        "unsupported_binder_type",
        "no_supported_type_occurrence",
    }
)
_LOW_VALUE_MUTATION_MODES = frozenset({"peer_premise"})
_LOW_VALUE_MUTATION_RULES = frozenset({"conjoin_peer_conclusion", "false_disjunct"})
_LOW_VALUE_MUTATION_TARGETS: frozenset[str] = frozenset()
_PRODUCTIVE_OPERATOR_NAMES = ("witness-relation",)
_PEER_OPERATOR_NAMES = frozenset({"conjoin", "strengthen"})
_LEAN_GATE_BATCH_ATTEMPTS = 8
_TOY_BASIC_SOURCE_PATHS = frozenset(
    {
        "Mathlib/Data/Bool/Basic.lean",
        "Mathlib/Data/Fin/Basic.lean",
        "Mathlib/Data/Nat/Basic.lean",
        "Mathlib/Data/Option/Basic.lean",
        "Mathlib/Logic/Basic.lean",
    }
)

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


def read_yield_history(path: Path) -> ProceduralYieldHistory:
    raw = path.read_bytes()
    source_families: Counter[str] = Counter()
    operator_chains: Counter[str] = Counter()
    entries = 0
    for no, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{no}: invalid procedural yield history row: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{path}:{no}: procedural yield history row must be an object")
        entries += 1
        _add_counter_map(source_families, payload.get("accepted_source_families"))
        _add_counter_map(operator_chains, payload.get("accepted_operator_chains"))
        if payload.get("accepted") is True:
            source_family = str(payload.get("source_family") or "").strip()
            operator_chain = str(payload.get("operator_chain") or "").strip()
            if source_family:
                source_families[source_family] += 1
            if operator_chain:
                operator_chains[operator_chain] += 1
    return ProceduralYieldHistory(
        sha256=hashlib.sha256(raw).hexdigest(),
        entries=entries,
        accepted_source_families=dict(source_families),
        accepted_operator_chains=dict(operator_chains),
    )


def _add_counter_map(counter: Counter[str], raw: object) -> None:
    if not isinstance(raw, dict):
        return
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value > 0:
            counter[key] += value


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
    allow_partial: bool = False,
    min_count: int = 1,
    max_queue_depth: int | None = None,
    citation_alpha: float = 0.5,
    citation_weight_cap: float = 64.0,
    citation_window_tempos: int = 2000,
    yield_history: ProceduralYieldHistory | None = None,
    import_graph: ImportGraph | None = None,
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
        yield_history=yield_history,
    )
    ctx = _Depth2GenerationContext(
        ordered=ordered,
        generation_seed=generation_seed,
        epoch_fields=epoch_fields,
        pool_hash=pool_hash,
        pool_receipt=pool_receipt,
        yield_history_metadata=yield_history.metadata() if yield_history is not None else {},
        operator_hash=operator_hash,
        tempo=tempo,
        import_graph=import_graph,
        mutation_engine=mutation_engine or PreviewMutationEngine(),
        gate_runner=gate_runner or AssumedProceduralGateRunner(),
        require_serious_candidates=bool(getattr(gate_runner, "requires_serious_candidates", False)),
    )
    workers = _resolve_generation_workers(generation_workers)
    attempt_limit = count * 50
    required_count = min(max(1, min_count), count) if allow_partial else count
    if workers <= 1:
        return _generate_depth2_candidates_sequential(
            ctx,
            count=count,
            required_count=required_count,
            attempt_limit=attempt_limit,
        )
    return _generate_depth2_candidates_parallel(
        ctx,
        count=count,
        required_count=required_count,
        attempt_limit=attempt_limit,
        workers=workers,
    )


def _generate_depth2_candidates_sequential(
    ctx: _Depth2GenerationContext,
    *,
    count: int,
    required_count: int,
    attempt_limit: int,
) -> tuple[TaskCandidate, ...]:
    out: list[TaskCandidate] = []
    seen: set[str] = set()
    seen_prelean: set[str] = set()
    telemetry = _Depth2Telemetry()
    cursor = 0
    while len(out) < count and cursor < attempt_limit:
        attempt = _attempt_depth2_candidate(
            ctx,
            cursor=cursor,
            seen_canonical_hashes=frozenset(seen),
            seen_prelean_keys=seen_prelean,
        )
        accepted = False
        if attempt.candidate is not None and attempt.verdict is not None:
            accepted = _maybe_accept_depth2_attempt(
                out,
                seen,
                attempt.candidate,
                attempt.verdict,
            )
        _record_depth2_attempt(telemetry, attempt, accepted=accepted)
        cursor += 1
    _log_depth2_telemetry(telemetry, count=count, attempt_limit=attempt_limit)
    if len(out) < required_count:
        raise ValueError(f"procedural gates accepted {len(out)} candidates, needed {required_count}")
    return _with_generation_count_receipt(
        tuple(out),
        target_count=count,
        attempt_count=telemetry.attempts,
        attempt_limit=attempt_limit,
    )


def _generate_depth2_candidates_parallel(
    ctx: _Depth2GenerationContext,
    *,
    count: int,
    required_count: int,
    attempt_limit: int,
    workers: int,
) -> tuple[TaskCandidate, ...]:
    out: list[TaskCandidate] = []
    seen: set[str] = set()
    seen_prelean: set[str] = set()
    telemetry = _Depth2Telemetry()
    cursor = 0
    while len(out) < count and cursor < attempt_limit:
        batch_width = _gate_batch_width(ctx.gate_runner, workers)
        batch_end = min(cursor + batch_width, attempt_limit)
        seen_snapshot = frozenset(seen)
        batch_cursors = range(cursor, batch_end)
        attempts = _attempt_depth2_candidates_parallel(
            ctx,
            batch_cursors,
            seen_snapshot,
            seen_prelean_keys=seen_prelean,
            workers=workers,
        )
        for attempt in sorted(attempts, key=lambda item: item.cursor):
            accepted = False
            if attempt.candidate is None or attempt.verdict is None:
                _record_depth2_attempt(telemetry, attempt, accepted=False)
                continue
            if len(out) < count:
                accepted = _maybe_accept_depth2_attempt(
                    out,
                    seen,
                    attempt.candidate,
                    attempt.verdict,
                )
            _record_depth2_attempt(telemetry, attempt, accepted=accepted)
        cursor = batch_end
    _log_depth2_telemetry(telemetry, count=count, attempt_limit=attempt_limit)
    if len(out) < required_count:
        raise ValueError(f"procedural gates accepted {len(out)} candidates, needed {required_count}")
    return _with_generation_count_receipt(
        tuple(out),
        target_count=count,
        attempt_count=telemetry.attempts,
        attempt_limit=attempt_limit,
    )


def _with_generation_count_receipt(
    candidates: tuple[TaskCandidate, ...],
    *,
    target_count: int,
    attempt_count: int,
    attempt_limit: int,
) -> tuple[TaskCandidate, ...]:
    accepted_count = len(candidates)
    return tuple(
        candidate.model_copy(
            update={
                "metadata": {
                    **candidate.metadata,
                    "procedural_generation_target_count": target_count,
                    "procedural_generation_accepted_count": accepted_count,
                    "procedural_generation_attempt_count": attempt_count,
                    "procedural_generation_attempt_limit": attempt_limit,
                }
            }
        )
        for candidate in candidates
    )


def _gate_batch_width(gate_runner: ProceduralGateRunner, workers: int) -> int:
    batch_capacity = getattr(gate_runner, "batch_capacity", None)
    if callable(batch_capacity):
        return max(workers, min(_LEAN_GATE_BATCH_ATTEMPTS, int(batch_capacity(workers))))
    if callable(getattr(gate_runner, "batch", None)):
        return max(workers, _LEAN_GATE_BATCH_ATTEMPTS)
    return workers


def _attempt_depth2_candidates_parallel(
    ctx: _Depth2GenerationContext,
    cursors: range,
    seen_snapshot: frozenset[str],
    *,
    seen_prelean_keys: set[str],
    workers: int,
) -> tuple[_Depth2Attempt, ...]:
    if not cursors:
        return ()
    worker_count = min(workers, len(cursors))
    batch_gate = getattr(ctx.gate_runner, "batch", None)
    if callable(batch_gate):
        mutation_attempts = _attempt_depth2_candidate_mutations_parallel(ctx, cursors, workers=worker_count)
        mutation_attempts = _mark_prelean_duplicates(mutation_attempts, seen_prelean_keys)
        candidates = tuple(
            attempt.candidate
            for attempt in mutation_attempts
            if attempt.candidate is not None and attempt.rejection_reason is None
        )
        verdicts = batch_gate(candidates, seen_canonical_hashes=seen_snapshot) if candidates else ()
        verdict_iter = iter(verdicts)
        batch_out: list[_Depth2Attempt] = []
        for attempt in mutation_attempts:
            if attempt.candidate is None or attempt.rejection_reason is not None:
                batch_out.append(attempt)
            else:
                batch_out.append(
                    _Depth2Attempt(cursor=attempt.cursor, candidate=attempt.candidate, verdict=next(verdict_iter))
                )
        return tuple(batch_out)

    mutation_attempts = _mark_prelean_duplicates(
        _attempt_depth2_candidate_mutations_parallel(ctx, cursors, workers=worker_count),
        seen_prelean_keys,
    )
    fallback_out = list(mutation_attempts)
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {
            pool.submit(ctx.gate_runner, attempt.candidate, seen_canonical_hashes=seen_snapshot): index
            for index, attempt in enumerate(fallback_out)
            if attempt.candidate is not None and attempt.rejection_reason is None
        }
        for future in as_completed(futures):
            index = futures[future]
            attempt = fallback_out[index]
            fallback_out[index] = _Depth2Attempt(
                cursor=attempt.cursor,
                candidate=attempt.candidate,
                verdict=future.result(),
            )
    return tuple(fallback_out)


def _attempt_depth2_candidate_mutations_parallel(
    ctx: _Depth2GenerationContext,
    cursors: range,
    *,
    workers: int,
) -> tuple[_Depth2Attempt, ...]:
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_candidate_attempt, ctx, cursor=cursor): cursor for cursor in cursors}
        attempts: list[_Depth2Attempt] = []
        for future in as_completed(futures):
            attempts.append(future.result())
    return tuple(sorted(attempts, key=lambda item: item.cursor))


def _attempt_depth2_candidate(
    ctx: _Depth2GenerationContext,
    *,
    cursor: int,
    seen_canonical_hashes: frozenset[str],
    seen_prelean_keys: set[str],
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
            yield_history_metadata=ctx.yield_history_metadata,
            operator_bundle_hash=ctx.operator_hash,
            tempo=ctx.tempo,
            sequence=cursor,
            import_graph=ctx.import_graph,
        )
    except ValueError as exc:
        return _Depth2Attempt(cursor=cursor, candidate=None, verdict=None, rejection_reason=_mutation_rejection(exc))
    prelean_rejection = _prelean_candidate_rejection(candidate, require_serious=ctx.require_serious_candidates)
    if prelean_rejection:
        return _Depth2Attempt(cursor=cursor, candidate=candidate, verdict=None, rejection_reason=prelean_rejection)
    duplicate_rejection = _prelean_duplicate_rejection(candidate, seen_prelean_keys)
    if duplicate_rejection:
        return _Depth2Attempt(cursor=cursor, candidate=candidate, verdict=None, rejection_reason=duplicate_rejection)
    verdict = ctx.gate_runner(candidate, seen_canonical_hashes=seen_canonical_hashes)
    return _Depth2Attempt(cursor=cursor, candidate=candidate, verdict=verdict)


def _candidate_attempt(ctx: _Depth2GenerationContext, *, cursor: int) -> _Depth2Attempt:
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
            yield_history_metadata=ctx.yield_history_metadata,
            operator_bundle_hash=ctx.operator_hash,
            tempo=ctx.tempo,
            sequence=cursor,
            import_graph=ctx.import_graph,
        )
    except ValueError as exc:
        return _Depth2Attempt(cursor=cursor, candidate=None, verdict=None, rejection_reason=_mutation_rejection(exc))
    prelean_rejection = _prelean_candidate_rejection(candidate, require_serious=ctx.require_serious_candidates)
    if prelean_rejection:
        return _Depth2Attempt(cursor=cursor, candidate=candidate, verdict=None, rejection_reason=prelean_rejection)
    return _Depth2Attempt(cursor=cursor, candidate=candidate, verdict=None)


def _mark_prelean_duplicates(
    attempts: tuple[_Depth2Attempt, ...],
    seen_prelean_keys: set[str],
) -> tuple[_Depth2Attempt, ...]:
    out: list[_Depth2Attempt] = []
    for attempt in attempts:
        if attempt.candidate is None or attempt.rejection_reason is not None:
            out.append(attempt)
            continue
        reason = _prelean_duplicate_rejection(attempt.candidate, seen_prelean_keys)
        out.append(
            _Depth2Attempt(cursor=attempt.cursor, candidate=attempt.candidate, verdict=None, rejection_reason=reason)
            if reason
            else attempt
        )
    return tuple(out)


def _maybe_accept_depth2_attempt(
    out: list[TaskCandidate],
    seen: set[str],
    candidate: TaskCandidate,
    verdict: ProceduralGateVerdict,
) -> bool:
    candidate = _with_gate_receipt(candidate, verdict)
    canonical_hash = str(candidate.metadata["canonical_hash"])
    if not verdict.accepted or canonical_hash in seen:
        return False
    seen.add(canonical_hash)
    out.append(candidate)
    return True


def _record_depth2_attempt(telemetry: _Depth2Telemetry, attempt: _Depth2Attempt, *, accepted: bool) -> None:
    telemetry.attempts += 1
    chain = _attempt_operator_chain(attempt)
    source_family = _attempt_source_family(attempt)
    if chain:
        telemetry.operator_chains[chain] += 1
    if source_family:
        telemetry.source_families[source_family] += 1
    if accepted:
        telemetry.accepted += 1
        if chain:
            telemetry.accepted_operator_chains[chain] += 1
        if source_family:
            telemetry.accepted_source_families[source_family] += 1
        return
    telemetry.rejected[_depth2_rejection_reason(attempt)] += 1


def _depth2_rejection_reason(attempt: _Depth2Attempt) -> str:
    if attempt.candidate is None or attempt.verdict is None:
        return attempt.rejection_reason or "mutation_failed"
    verdict = attempt.verdict
    if not verdict.typechecked:
        return f"typecheck:{verdict.metadata.get('typecheck_reason') or 'failed'}"
    if not verdict.prop_gate_passed:
        return f"prop_gate:{verdict.metadata.get('prop_gate_reason') or 'failed'}"
    if verdict.novelty_status != "passed":
        return f"novelty:{verdict.novelty_status}"
    if not verdict.triviality_checked:
        return f"triviality:{verdict.metadata.get('triviality_reason') or 'not_checked'}"
    if verdict.baseline_solved:
        return f"baseline:{verdict.metadata.get('baseline_solver') or 'solved'}"
    return "not_accepted"


def _mutation_rejection(exc: ValueError) -> str:
    message = str(exc)
    if ":" in message:
        message = message.split(":", 1)[0]
    return f"mutation:{message or 'failed'}"


def _attempt_operator_chain(attempt: _Depth2Attempt) -> str:
    if attempt.candidate is None:
        return ""
    chain = attempt.candidate.metadata.get("mutation_chain")
    if not isinstance(chain, list):
        return ""
    operators = [str(step.get("operator") or "") for step in chain if isinstance(step, dict)]
    return ",".join(operator for operator in operators if operator)


def _attempt_source_family(attempt: _Depth2Attempt) -> str:
    if attempt.candidate is None:
        return ""
    return _source_family(attempt.candidate)


def _top_counter(counter: Counter[str], *, limit: int = 8) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:limit])


def _log_depth2_telemetry(telemetry: _Depth2Telemetry, *, count: int, attempt_limit: int) -> None:
    if os.environ.get("LEMMA_PROCEDURAL_GENERATION_TELEMETRY", "").strip().lower() not in {"1", "true", "yes"}:
        return
    logger.info(
        "procedural_generation_attempts {}",
        json.dumps(
            {
                "accepted": telemetry.accepted,
                "attempt_limit": attempt_limit,
                "attempts": telemetry.attempts,
                "acceptance_rate": round(telemetry.accepted / telemetry.attempts, 4) if telemetry.attempts else 0.0,
                "accepted_operator_chains": _top_counter(telemetry.accepted_operator_chains),
                "accepted_source_families": _top_counter(telemetry.accepted_source_families),
                "needed": count,
                "operator_chains": _top_counter(telemetry.operator_chains),
                "rejected": dict(sorted(telemetry.rejected.items())),
                "source_families": _top_counter(telemetry.source_families),
            },
            sort_keys=True,
        ),
    )


def _prelean_candidate_rejection(candidate: TaskCandidate, *, require_serious: bool = False) -> str:
    metadata = candidate.metadata
    chain = metadata.get("mutation_chain")
    if not isinstance(chain, list) or len(chain) != 2:
        return "prelean:mutation_chain"
    for step in chain:
        if not isinstance(step, dict):
            return "prelean:mutation_chain"
        operator = step.get("operator")
        if operator not in OPERATOR_NAMES:
            return "prelean:operator"
        params = step.get("params")
        if not isinstance(params, dict):
            return "prelean:mutation_params"
        engine = params.get("engine")
        if isinstance(engine, str) and engine and engine != MUTATION_ENGINE:
            return "prelean:mutation_engine"
        input_hash = str(step.get("input_hash") or "")
        output_hash = str(step.get("output_hash") or "")
        if not _HEX64.fullmatch(input_hash) or not _HEX64.fullmatch(output_hash):
            return "prelean:mutation_hash"
        if input_hash == output_hash:
            return "prelean:no_op"
    if len(set(candidate.imports)) != len(candidate.imports):
        return "prelean:duplicate_import"
    if any(not _LEAN_MODULE.fullmatch(module) for module in candidate.imports):
        return "prelean:import_module"
    if require_serious:
        task_pool = parse_task_pool(metadata.get("task_pool"))
        if task_pool not in {TaskPool.SERIOUS_PAID, TaskPool.FRONTIER}:
            return f"prelean:task_pool:{task_pool.value}"
    return ""


def _prelean_duplicate_rejection(candidate: TaskCandidate, seen_prelean_keys: set[str]) -> str:
    key = _prelean_statement_key(candidate)
    if key in seen_prelean_keys:
        return "prelean:duplicate_statement"
    seen_prelean_keys.add(key)
    return ""


def _prelean_statement_key(candidate: TaskCandidate) -> str:
    return statement_hash(_normalize_generated_binders(candidate.type_expr))


def _normalize_generated_binders(type_expr: str) -> str:
    text = type_expr.strip()
    match = _GENERATED_PROP_BINDER.fullmatch(text)
    if match is None:
        return text
    return f"∀ lemma_p : Prop, lemma_p → ({match.group(2).strip()})"


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
    yield_history_metadata: dict[str, object] | None = None,
    operator_bundle_hash: str,
    tempo: int,
    sequence: int,
    import_graph: ImportGraph | None = None,
    operator_chain: tuple[str, ...] | None = None,
) -> TaskCandidate:
    type_expr = source.type_expr.strip()
    imports = _challenge_imports_for_source(source, import_graph)
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
        peer = (
            _peer_source(source_pool, source_id=source.id, seed=generation_seed, sequence=sequence, step=step)
            if operator in _PEER_OPERATOR_NAMES
            else source
        )
        mutation = mutation_engine.apply(
            source,
            type_expr,
            operator,
            step=step,
            param_seed=_hash_text(f"{generation_seed}:{sequence}:{step}:{operator}"),
            peer=peer,
        )
        if _mutation_uses_peer(mutation.params):
            imports = _combined_imports(imports, peer.imports)
        _reject_low_value_mutation(mutation.params)
        if "sorry" in mutation.type_expr or "?_" in mutation.type_expr:
            raise ValueError("invalid procedural mutation output: placeholder")
        output_hash = _hash_text(mutation.type_expr)
        if output_hash == input_hash:
            raise ValueError("low-value procedural mutation: no-op")
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
        **(yield_history_metadata or {}),
        "operator_bundle_version": OPERATOR_BUNDLE_VERSION,
        "operator_bundle_hash": operator_bundle_hash,
        "canonical_hash": canonical_hash,
        "statement_hash": mutated_statement_hash,
        "license_state": license_state_for(source.source_license, str(source.metadata.get("license_state") or "")),
        "source_task_id": source.id,
        "source_theorem_name": source.theorem_name,
        "source_target_sha256": _hash_text(source.statement),
    }
    metadata["source_import_status"] = source_import_status(imports, metadata, source_path=source.source_ref.path)
    for key in (
        "topic",
        "subtopic",
        "citation_weight",
        "direct_dependency_count",
        "dependency_depth",
        "transitive_dependency_hash",
        "lemma_rows_used",
        "substrate_row_id",
    ):
        if key in source.metadata:
            metadata[key] = source.metadata[key]
    metadata.update(source_pricing_metadata("procedural", metadata))
    return TaskCandidate(
        id=f"lemma.procedural.{canonical_hash[:16]}",
        title=source.title or source.theorem_name,
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


def _mutation_uses_peer(params: dict[str, object]) -> bool:
    return any(key in params for key in ("peer_source_id", "peer_theorem_name", "peer_target_sha256"))


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


def _operator_for_step(seed: str, sequence: int, step: int, type_expr: str) -> str | None:
    if step > 0:
        return "specialize" if _supports_specialize(type_expr) else None
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
        and _supports_depth2_chain(source.type_expr)
        and _not_toy_basic_source(source)
    )


def _supports_depth2_chain(type_expr: str) -> bool:
    return bool(_productive_operators_for(type_expr)) and _supports_specialize(type_expr)


def _productive_operators_for(type_expr: str) -> tuple[str, ...]:
    from lemma.supply.mutation import _split_forall_prefix, _split_top_level_relation

    _prefix, body = _split_forall_prefix(type_expr)
    return _PRODUCTIVE_OPERATOR_NAMES if _split_top_level_relation(body) is not None else ()


def _supports_specialize(type_expr: str) -> bool:
    from lemma.supply.mutation import _split_forall

    binder = _split_forall(type_expr)
    if binder is None:
        return False
    _name, binder_type, body = binder
    remaining = body.strip()
    return (
        binder_type.strip() in SMALL_VALUES_BY_TYPE
        and remaining.startswith("∀")
        and not remaining[1:].lstrip().startswith("[")
    )


def _not_toy_basic_source(source: TaskCandidate) -> bool:
    return str(source.source_ref.path or "").strip() not in _TOY_BASIC_SOURCE_PATHS


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


def _challenge_imports_for_source(source: TaskCandidate, import_graph: ImportGraph | None) -> tuple[str, ...]:
    module = _source_module_from_path(source.source_ref.path)
    if import_graph is not None and module is not None and module in import_graph.edges:
        return import_graph.edges[module]
    return source.imports


def _source_module_from_path(path: str | None) -> str | None:
    if path is None:
        return None
    text = path.strip()
    if not text.endswith(".lean") or text.startswith("/") or "\\" in text:
        return None
    parts = text.removesuffix(".lean").split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    return ".".join(parts)


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
    yield_history: ProceduralYieldHistory | None = None,
) -> tuple[TaskCandidate, ...]:
    alpha = min(1.0, max(0.0, float(citation_alpha)))
    cap = max(1.0, float(citation_weight_cap))
    uniform = sorted(
        sources,
        key=lambda source: (
            -_yield_source_score(source, yield_history),
            _hash_text(f"{seed}:uniform:{source.id}:{source.type_expr}"),
        ),
    )
    weighted = sorted(
        sources,
        key=lambda source: _weighted_source_key(source, seed=seed, cap=cap, yield_history=yield_history),
    )
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
    return _source_family_balanced_sources(_depth_balanced_sources(tuple(out)))


def _depth_balanced_sources(sources: tuple[TaskCandidate, ...]) -> tuple[TaskCandidate, ...]:
    buckets: dict[int, list[TaskCandidate]] = {}
    for source in sources:
        buckets.setdefault(source.queue_depth, []).append(source)
    depths = depth_spread_order(tuple(buckets))
    out: list[TaskCandidate] = []
    index = 0
    while len(out) < len(sources):
        for depth in depths:
            bucket = buckets[depth]
            if index < len(bucket):
                out.append(bucket[index])
        index += 1
    return tuple(out)


def _source_family_balanced_sources(sources: tuple[TaskCandidate, ...]) -> tuple[TaskCandidate, ...]:
    buckets: dict[str, list[TaskCandidate]] = {}
    for source in sources:
        buckets.setdefault(_source_family(source), []).append(source)
    families = tuple(buckets)
    out: list[TaskCandidate] = []
    index = 0
    while len(out) < len(sources):
        for family in families:
            bucket = buckets[family]
            if index < len(bucket):
                out.append(bucket[index])
        index += 1
    return tuple(out)


def _next_unused(candidates: Iterator[TaskCandidate], used: set[str]) -> TaskCandidate | None:
    for source in candidates:
        if source.id not in used:
            return source
    return None


def _weighted_source_key(
    source: TaskCandidate,
    *,
    seed: str,
    cap: float,
    yield_history: ProceduralYieldHistory | None,
) -> tuple[int, float, str]:
    raw_weight = _metadata_float(source.metadata.get("citation_weight"))
    weight = min(cap, raw_weight if raw_weight is not None else 1.0)
    if weight <= 0:
        return -_yield_source_score(source, yield_history), math.inf, source.id
    return (
        -_yield_source_score(source, yield_history),
        -math.log(_unit_interval(f"{seed}:weighted:{source.id}:{source.type_expr}")) / weight,
        source.id,
    )


def _yield_source_score(source: TaskCandidate, yield_history: ProceduralYieldHistory | None) -> int:
    if yield_history is None:
        return 0
    source_score = yield_history.accepted_source_families.get(_source_family(source), 0)
    chain_score = 0
    for operator in _productive_operators_for(source.type_expr):
        chain_score = max(chain_score, yield_history.accepted_operator_chains.get(f"{operator},specialize", 0))
    return (source_score * 1_000) + chain_score


def _source_family(source: TaskCandidate) -> str:
    topic = str(source.metadata.get("topic") or "").strip()
    subtopic = str(source.metadata.get("subtopic") or "").strip()
    if topic and subtopic:
        return f"{topic}/{subtopic}"
    if topic:
        return topic
    path = str(source.source_ref.path or "").strip()
    if path:
        parts = path.removesuffix(".lean").split("/")
        if len(parts) >= 3 and parts[0] == "Mathlib":
            if parts[1] == "Data":
                return "/".join(parts[1:3])
            return parts[1]
        return path
    return str(source.source_ref.name or source.id)


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
