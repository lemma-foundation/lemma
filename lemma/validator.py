"""Validator-side verification, scoring, and corpus writing."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from lemma.chain.commitments import ChainCommitmentSubmission
from lemma.chain.epoch_randomness import resolve_chain_drand_epoch_randomness
from lemma.chain.weights import ChainWeightSubmission
from lemma.common.config import LemmaSettings
from lemma.corpus import CorpusRow, build_corpus_row, write_corpus_index, write_jsonl
from lemma.lean.proof_identity import proof_identity
from lemma.lean.sandbox import VerifyResult
from lemma.protocol_invariants import enforce_production_invariants
from lemma.scoring import ScoreResult, UnearnedPolicy, VerificationRecord, score_epoch
from lemma.store import append_jsonl
from lemma.submissions import LemmaSubmission, validate_submission_for_task
from lemma.supply.controller import CurriculumTempoRecord
from lemma.supply.queue import initial_active_pool
from lemma.supply.slot_weight import slot_weight_receipt_for_kernel_dependencies, slot_weight_receipt_for_task
from lemma.task_activation import task_reward_eligibility, task_slot_weight
from lemma.tasks import LemmaTask, TaskRegistry, fetch_task_registry, load_task_registry, task_registry_from_tasks
from lemma.verifiers.lean import verify_result_from_adapter_result
from lemma.verifiers.registry import get_verifier

VerifySubmission = Callable[[LemmaTask, LemmaSubmission], VerifyResult]
SubmitWeights = Callable[[LemmaSettings, dict[str, float]], ChainWeightSubmission]
SubmitCommitment = Callable[[LemmaSettings, str], ChainCommitmentSubmission]
ChainAuthenticatedKey = tuple[str, str, str]


class ValidatorRunSummary(BaseModel):
    """Public-safe summary of one validator pass."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    run_at: str
    registry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    active_K: int = Field(ge=0)
    active_tempo: int | None = Field(default=None, ge=0)
    active_seed_mode: Literal["static", "epoch_randomness"] = "static"
    active_epoch_randomness_source: Literal["manual", "chain_drand"] = "manual"
    active_epoch_randomness_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    active_selection_seed_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    active_task_ids: tuple[str, ...] = ()
    active_pool_directory_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    active_pool_merkle_root: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    accepted_merkle_root: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    accepted_directory_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    tempo_commitment_payload: str = ""
    chain_commitment_set: bool = False
    canonical_publish_uri: str = ""
    canonical_publish_count: int = Field(default=0, ge=0)
    frontier_depth: int = Field(ge=0)
    verified_count: int = Field(ge=0)
    accepted_unique_count: int = Field(ge=0)
    rewarded_count: int = Field(ge=0)
    score_event_count: int = Field(ge=0)
    corpus_row_count: int = Field(ge=0)
    unearned_share: float = Field(ge=0.0, le=1.0)
    unearned_policy: UnearnedPolicy
    weights_set: bool
    chain_weight_uids: tuple[int, ...] | None = None
    chain_weight_values: tuple[float, ...] | None = None


@dataclass(frozen=True)
class ValidatorRunResult:
    verification_records: tuple[VerificationRecord, ...]
    score: ScoreResult
    corpus_rows: tuple[CorpusRow, ...]
    weights_set: bool
    weight_submission: ChainWeightSubmission | None
    commitment_submission: ChainCommitmentSubmission | None
    summary: ValidatorRunSummary


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def current_active_tempo(settings: LemmaSettings, *, now: datetime | None = None) -> int:
    """Return the active chain tempo index."""
    if settings.active_tempo_source == "chain":
        import bittensor as bt

        subtensor = bt.Subtensor(network=settings.bt_network or None)
        block = int(subtensor.get_current_block())
        hyperparams = subtensor.get_subnet_hyperparameters(settings.netuid, block=block)
        chain_tempo_blocks = int(cast(Any, hyperparams).tempo)
        if chain_tempo_blocks <= 0:
            raise RuntimeError("chain tempo must be positive")
        return block // chain_tempo_blocks
    instant = now or datetime.now(UTC)
    return int(instant.timestamp() // settings.active_tempo_seconds)


def _hash_payload(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def resolve_active_epoch_randomness(settings: LemmaSettings, *, tempo: int) -> str:
    """Return the public randomness material for one active epoch."""
    if settings.active_epoch_randomness_source == "manual":
        randomness = settings.active_epoch_randomness.strip()
        if not randomness:
            raise RuntimeError("epoch-randomness active selection requires LEMMA_ACTIVE_EPOCH_RANDOMNESS")
        return randomness
    return resolve_chain_drand_epoch_randomness(settings, tempo=tempo).seed_material()


def active_epoch_seed(settings: LemmaSettings, *, tempo: int, epoch_randomness: str | None = None) -> str:
    """Return the seed that paid procedural tasks must be generated from."""
    if settings.protocol_mode == "production" and settings.active_seed_mode != "epoch_randomness":
        raise RuntimeError("production mode requires LEMMA_ACTIVE_SEED_MODE=epoch_randomness")
    if settings.active_seed_mode == "static":
        return settings.active_queue_seed
    randomness = epoch_randomness or resolve_active_epoch_randomness(settings, tempo=tempo)
    return _hash_payload(
        {
            "version": "lemma-epoch-seed-v1",
            "netuid": settings.netuid,
            "tempo": tempo,
            "salt": settings.active_queue_seed,
            "epoch_randomness": randomness,
        }
    )


def active_selection_seed(
    registry: TaskRegistry, settings: LemmaSettings, *, tempo: int, epoch_randomness: str | None = None
) -> str:
    """Return the deterministic active-window seed for one registry and tempo."""
    epoch_seed = active_epoch_seed(settings, tempo=tempo, epoch_randomness=epoch_randomness)
    if settings.active_seed_mode == "static":
        return epoch_seed
    return _hash_payload(
        {
            "version": "lemma-active-selection-v1",
            "epoch_seed": epoch_seed,
            "registry_sha256": registry.sha256,
            "frontier_depth": settings.frontier_depth,
        }
    )


def active_selection_seed_sha256(
    registry: TaskRegistry, settings: LemmaSettings, *, tempo: int, epoch_randomness: str | None = None
) -> str:
    return hashlib.sha256(
        active_selection_seed(registry, settings, tempo=tempo, epoch_randomness=epoch_randomness).encode()
    ).hexdigest()


def active_epoch_randomness_sha256(
    settings: LemmaSettings, *, tempo: int, epoch_randomness: str | None = None
) -> str | None:
    if settings.active_seed_mode == "static":
        return None
    randomness = epoch_randomness or resolve_active_epoch_randomness(settings, tempo=tempo)
    return hashlib.sha256(randomness.encode()).hexdigest()


def task_registry_for_validation(settings: LemmaSettings, *, tempo: int) -> TaskRegistry:
    """Load the active task registry for the configured supply mode."""
    settings = curriculum_controlled_settings(settings, tempo=tempo)
    if settings.protocol_mode == "production" and settings.task_supply_mode != "procedural":
        raise RuntimeError("production mode requires LEMMA_TASK_SUPPLY_MODE=procedural")
    if settings.task_supply_mode == "registry":
        return fetch_task_registry(
            settings,
            verify_signature=settings.verify_registry_signatures,
        )
    cached = cached_active_registry_for_tempo(settings, tempo=tempo)
    if cached is not None:
        return cached
    if settings.active_registry_role == "auditor":
        raise RuntimeError(
            "active registry auditor mode requires a current active-registry cache; refusing local generation"
        )
    return _procedural_registry_for_tempo(settings, tempo=tempo)


def active_registry_cache_path(settings: LemmaSettings, *, tempo: int) -> Path | None:
    if settings.active_registry_json is not None:
        return settings.active_registry_json
    if settings.active_registry_cache_dir is None:
        return None
    return settings.active_registry_cache_dir / f"tempo-{tempo}.registry.json"


def cached_active_registry_for_tempo(settings: LemmaSettings, *, tempo: int) -> TaskRegistry | None:
    path = active_registry_cache_path(settings, tempo=tempo)
    if path is None:
        return None
    if not path.is_file():
        if settings.active_registry_json is not None:
            raise RuntimeError(f"active registry file does not exist: {path}")
        return None
    registry = load_task_registry(path.read_bytes())
    curriculum_state_replay = (
        settings.curriculum_retarget_enabled
        and settings.curriculum_state_jsonl is not None
        and settings.curriculum_state_jsonl.exists()
    )
    if (
        settings.active_registry_json is None
        and curriculum_state_replay
        and active_registry_cache_stale(registry, settings)
    ):
        return None
    return registry


def active_registry_cache_stale(registry: TaskRegistry, settings: LemmaSettings) -> bool:
    expected_count = settings.procedural_candidate_count or settings.active_task_count
    if len(registry.tasks) != expected_count:
        return True
    if any(task.frontier_depth != settings.frontier_depth for task in registry.tasks):
        return True
    expected_source = (settings.procedural_source_sha256_expected or "").strip().lower().removeprefix("sha256:")
    if expected_source and {str(task.metadata.get("source_pool_hash") or "") for task in registry.tasks} != {
        expected_source
    }:
        return True
    expected_operator = (settings.procedural_operator_bundle_sha256_expected or "").strip().lower().removeprefix(
        "sha256:"
    )
    if expected_operator and {str(task.metadata.get("operator_bundle_hash") or "") for task in registry.tasks} != {
        expected_operator
    }:
        return True
    return False


def curriculum_controlled_settings(settings: LemmaSettings, *, tempo: int) -> LemmaSettings:
    if not settings.curriculum_retarget_enabled or settings.curriculum_state_jsonl is None:
        return settings
    if settings.protocol_mode == "production":
        if not settings.curriculum_state_public:
            raise RuntimeError(
                "production curriculum retargeting requires LEMMA_CURRICULUM_STATE_PUBLIC=1 "
                "and a published/replayed LEMMA_CURRICULUM_STATE_JSONL"
            )
        canonical_state_jsonl = (
            _canonical_output_root(settings) / _netuid_label(settings) / "curriculum" / "curriculum.jsonl"
        )
        if settings.curriculum_state_jsonl.expanduser().resolve() == canonical_state_jsonl.expanduser().resolve():
            raise RuntimeError(
                "production curriculum retargeting requires LEMMA_CURRICULUM_STATE_JSONL "
                "to be a replay cache outside canonical publish output"
            )
    from lemma.supply.controller import read_curriculum_records

    # Retarget rows are produced after validating a tempo, so they activate with
    # one extra tempo of public replay lag to avoid mid-epoch active-set changes.
    records = tuple(
        record for record in read_curriculum_records(settings.curriculum_state_jsonl) if record.tempo < tempo - 1
    )
    if not records:
        return settings
    latest = records[-1]
    return settings.model_copy(
        update={
            "active_task_count": latest.active_K,
            "frontier_depth": latest.frontier_depth,
        }
    )


def _procedural_registry_for_tempo(settings: LemmaSettings, *, tempo: int) -> TaskRegistry:
    from lemma.supply.gates import LeanProceduralGateRunner
    from lemma.supply.import_graph import empty_import_graph, read_import_graph
    from lemma.supply.mathlib_snapshot import candidates_from_jsonl as mathlib_candidates_from_jsonl
    from lemma.supply.mutation import StructuralMutationEngine
    from lemma.supply.novelty import empty_novelty_cache, read_novelty_cache
    from lemma.supply.procedural import (
        build_procedural_registry_tasks,
        corpus_sources_from_dir,
        generate_depth2_candidates,
        source_pool_hash,
    )
    from lemma.supply.triviality_budget import triviality_budget_receipt_for_settings

    if settings.procedural_source_jsonl is None:
        raise RuntimeError("procedural supply requires LEMMA_PROCEDURAL_SOURCE_JSONL")
    if settings.protocol_mode == "production" and settings.procedural_novelty_cache_jsonl is None:
        raise RuntimeError("production procedural supply requires LEMMA_PROCEDURAL_NOVELTY_CACHE_JSONL")
    if settings.protocol_mode == "production" and settings.procedural_import_graph_jsonl is None:
        raise RuntimeError("production procedural supply requires LEMMA_PROCEDURAL_IMPORT_GRAPH_JSONL")
    if settings.protocol_mode == "production":
        if settings.procedural_prior_corpus_dir is None:
            raise RuntimeError("production procedural supply requires LEMMA_PROCEDURAL_PRIOR_CORPUS_DIR")
        if not settings.procedural_prior_corpus_dir.is_dir():
            raise RuntimeError("LEMMA_PROCEDURAL_PRIOR_CORPUS_DIR must be a public substrate directory")
    source_limit = settings.procedural_source_limit or None
    sources = mathlib_candidates_from_jsonl(settings.procedural_source_jsonl, limit=source_limit)
    if settings.procedural_prior_corpus_dir is not None:
        sources = sources + corpus_sources_from_dir(
            settings.procedural_prior_corpus_dir,
            before_tempo=tempo,
            citation_window_tempos=settings.procedural_citation_window_tempos,
        )
    actual_source_hash = source_pool_hash(sources)
    expected_source_hash = (settings.procedural_source_sha256_expected or "").strip().lower().removeprefix("sha256:")
    if expected_source_hash and actual_source_hash != expected_source_hash:
        raise RuntimeError(
            f"procedural source pool sha256 mismatch: got {actual_source_hash}, expected {expected_source_hash}"
        )
    epoch_randomness = (
        resolve_active_epoch_randomness(settings, tempo=tempo)
        if settings.active_seed_mode == "epoch_randomness"
        else settings.active_queue_seed
    )
    generation_seed = active_epoch_seed(settings, tempo=tempo, epoch_randomness=epoch_randomness)
    count = settings.procedural_candidate_count or settings.active_task_count
    triviality_budget = triviality_budget_receipt_for_settings(settings, tempo=tempo)
    novelty_cache = (
        read_novelty_cache(settings.procedural_novelty_cache_jsonl)
        if settings.procedural_novelty_cache_jsonl is not None
        else empty_novelty_cache()
    )
    import_graph = (
        read_import_graph(settings.procedural_import_graph_jsonl)
        if settings.procedural_import_graph_jsonl is not None
        else empty_import_graph()
    )
    candidates = generate_depth2_candidates(
        sources,
        generation_seed=generation_seed,
        epoch_randomness=epoch_randomness,
        count=count,
        tempo=tempo,
        max_queue_depth=settings.frontier_depth,
        citation_alpha=settings.procedural_citation_alpha,
        citation_weight_cap=settings.procedural_citation_weight_cap,
        citation_window_tempos=settings.procedural_citation_window_tempos,
        mutation_engine=StructuralMutationEngine() if settings.protocol_mode == "production" else None,
        gate_runner=(
            LeanProceduralGateRunner(
                settings,
                triviality_budget_receipt=triviality_budget,
                novelty_cache=novelty_cache,
                import_graph=import_graph,
            )
            if settings.protocol_mode == "production"
            else None
        ),
        generation_workers=(
            None if settings.procedural_generation_workers <= 0 else settings.procedural_generation_workers
        ),
    )
    build = build_procedural_registry_tasks(candidates, seed=generation_seed, frontier_depth=settings.frontier_depth)
    if build.rejected:
        detail = ", ".join(f"{item.id}:{item.reason}" for item in build.rejected[:5])
        raise RuntimeError(f"procedural supply rejected generated candidates: {detail}")
    return task_registry_from_tasks(build.tasks)


def _enforce_epoch_generated_paid_tasks(
    tasks: Sequence[LemmaTask], expected_seed: str, epoch_randomness: str
) -> None:
    try:
        epoch_fields = json.loads(epoch_randomness)
    except json.JSONDecodeError:
        epoch_fields = {}
    expected_anchor_block = epoch_fields.get("anchor_block") if isinstance(epoch_fields, dict) else None
    expected_drand_round = epoch_fields.get("drand_round") if isinstance(epoch_fields, dict) else None
    mismatches: list[str] = []
    for task in tasks:
        if not task_reward_eligibility(task).eligible:
            continue
        metadata = task.metadata
        if metadata.get("generation_seed") != expected_seed:
            mismatches.append(f"{task.id}:generation_seed")
        if isinstance(expected_anchor_block, int) and metadata.get("anchor_block") != expected_anchor_block:
            mismatches.append(f"{task.id}:anchor_block")
        if isinstance(expected_drand_round, int) and metadata.get("drand_round") != expected_drand_round:
            mismatches.append(f"{task.id}:drand_round")
    if mismatches:
        detail = ", ".join(mismatches[:5])
        raise RuntimeError(f"production paid tasks must use active epoch randomness: {detail}")


def _weight_receipt(
    *,
    submitted_at: str,
    settings: LemmaSettings,
    registry_sha256: str,
    submission: ChainWeightSubmission,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema_version": 1,
        "submitted_at": submitted_at,
        "registry_sha256": registry_sha256,
        "netuid": settings.netuid,
        "bt_network": settings.bt_network or "default",
        "success": submission.success,
        "uids": submission.uids,
        "weights": submission.weights,
    }
    if submission.extrinsic_function:
        receipt["extrinsic_function"] = submission.extrinsic_function
    if submission.extrinsic_hash:
        receipt["extrinsic_hash"] = submission.extrinsic_hash
    if submission.block_hash:
        receipt["block_hash"] = submission.block_hash
    if submission.block_number is not None:
        receipt["block_number"] = submission.block_number
    if submission.extrinsic_fee_rao is not None:
        receipt["extrinsic_fee_rao"] = submission.extrinsic_fee_rao
    if submission.message:
        receipt["message"] = submission.message
    return receipt


def _commitment_receipt(
    *,
    submitted_at: str,
    settings: LemmaSettings,
    registry_sha256: str,
    submission: ChainCommitmentSubmission,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema_version": 1,
        "submitted_at": submitted_at,
        "registry_sha256": registry_sha256,
        "netuid": settings.netuid,
        "bt_network": settings.bt_network or "default",
        "success": submission.success,
        "payload": submission.payload,
        "hotkey": submission.hotkey,
    }
    if submission.extrinsic_function:
        receipt["extrinsic_function"] = submission.extrinsic_function
    if submission.extrinsic_hash:
        receipt["extrinsic_hash"] = submission.extrinsic_hash
    if submission.block_hash:
        receipt["block_hash"] = submission.block_hash
    if submission.block_number is not None:
        receipt["block_number"] = submission.block_number
    if submission.extrinsic_fee_rao is not None:
        receipt["extrinsic_fee_rao"] = submission.extrinsic_fee_rao
    if submission.message:
        receipt["message"] = submission.message
    return receipt


def _default_verify(settings: LemmaSettings) -> VerifySubmission:
    def verify(task: LemmaTask, submission: LemmaSubmission) -> VerifyResult:
        verifier = get_verifier(task.domain_id, settings=settings)
        return verify_result_from_adapter_result(verifier.verify(task, submission))

    return verify


def active_tasks_for_validation(
    registry: TaskRegistry,
    settings: LemmaSettings,
    *,
    tempo: int | None = None,
) -> tuple[LemmaTask, ...]:
    """Select the deterministic active K-window from a registry."""
    active_tempo = current_active_tempo(settings) if tempo is None else tempo
    settings = curriculum_controlled_settings(settings, tempo=active_tempo)
    candidates = tuple(task for task in registry.tasks if task.queue_depth <= settings.frontier_depth)
    active_k = min(settings.active_task_count, len(candidates))
    if active_k == 0:
        return ()
    epoch_randomness = (
        resolve_active_epoch_randomness(settings, tempo=active_tempo)
        if settings.active_seed_mode == "epoch_randomness"
        else None
    )
    if settings.protocol_mode == "production":
        if epoch_randomness is None:
            raise RuntimeError("production mode requires LEMMA_ACTIVE_SEED_MODE=epoch_randomness")
        _enforce_epoch_generated_paid_tasks(
            registry.tasks,
            active_epoch_seed(settings, tempo=active_tempo, epoch_randomness=epoch_randomness),
            epoch_randomness,
        )
    pool = initial_active_pool(
        candidates,
        active_K=active_k,
        tempo=active_tempo,
        seed=active_selection_seed(registry, settings, tempo=active_tempo, epoch_randomness=epoch_randomness),
        frontier_depth=settings.frontier_depth,
    )
    by_id = {task.id: task for task in pool.queue}
    return tuple(
        by_id[slot.task_id].model_copy(
            update={
                "queue_position": slot.queue_position,
                "queue_depth": slot.queue_depth,
                "frontier_depth": settings.frontier_depth,
            }
        )
        for slot in pool.slots
    )


def read_submissions_jsonl(path: Path) -> list[LemmaSubmission]:
    text = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        return [LemmaSubmission.model_validate(payload)]
    if isinstance(payload, list):
        return [LemmaSubmission.model_validate(item) for item in payload]
    if payload is not None:
        raise ValueError(f"{path}: expected submission object, array, or JSONL rows")

    rows: list[LemmaSubmission] = []
    for no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(LemmaSubmission.model_validate_json(line))
        except ValueError as e:
            raise ValueError(f"{path}:{no}: invalid submission: {e}") from e
    return rows


def pending_submission_files(spool_dir: Path) -> tuple[Path, ...]:
    """Return top-level pending submission files in deterministic order."""
    if not spool_dir.exists():
        return ()
    if not spool_dir.is_dir():
        raise ValueError(f"{spool_dir} is not a directory")
    return tuple(sorted(path for path in spool_dir.iterdir() if path.is_file() and path.suffix in {".json", ".jsonl"}))


def read_submission_spool(spool_dir: Path) -> tuple[list[LemmaSubmission], tuple[Path, ...]]:
    paths = pending_submission_files(spool_dir)
    submissions: list[LemmaSubmission] = []
    for path in paths:
        submissions.extend(read_submissions_jsonl(path))
    return submissions, paths


def archive_submission_spool(paths: Sequence[Path], spool_dir: Path) -> None:
    processed = spool_dir / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    for idx, path in enumerate(paths):
        if not path.exists():
            continue
        target = processed / f"{stamp}-{idx:04d}-{path.name}"
        path.replace(target)


def _next_local_epoch_file(corpus_dir: Path) -> Path:
    used: set[int] = set()
    for path in corpus_dir.glob("epoch-*.jsonl"):
        stem = path.stem.removeprefix("epoch-")
        if stem.isdigit():
            used.add(int(stem))
    next_epoch = 1
    while next_epoch in used:
        next_epoch += 1
    return corpus_dir / f"epoch-{next_epoch:06d}.jsonl"


def _existing_corpus_row_ids(corpus_dir: Path, *, tempo: int) -> set[str]:
    row_ids: set[str] = set()
    for path in sorted(corpus_dir.glob("epoch-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("tempo") != tempo:
                continue
            row_id = row.get("row_id")
            if isinstance(row_id, str) and row_id:
                row_ids.add(row_id)
    return row_ids


def _netuid_label(settings: LemmaSettings) -> str:
    return f"sn{settings.netuid}"


def _canonical_output_root(settings: LemmaSettings) -> Path:
    return settings.canonical_output_dir or (settings.operator_data_dir / "canonical")


def _write_public_tempo_artifacts(
    settings: LemmaSettings,
    *,
    active_tasks: Sequence[LemmaTask],
    rows: Sequence[CorpusRow],
    tempo: int,
    curriculum_records: Sequence[CurriculumTempoRecord] = (),
) -> dict[str, object]:
    from lemma.corpus.storage import (
        build_active_pool_storage,
        build_curriculum_state_storage,
        build_epoch_storage_from_rows,
    )

    output_root = _canonical_output_root(settings)
    netuid = _netuid_label(settings)
    resolver = "hippius-s3-arion"
    active_pool = build_active_pool_storage(active_tasks, output_root, netuid=netuid, tempo=tempo, resolver=resolver)
    accepted = build_epoch_storage_from_rows(
        rows,
        output_root,
        netuid=netuid,
        tempo=tempo,
        resolver=resolver,
        active_pool=active_pool,
    )
    curriculum = (
        build_curriculum_state_storage(curriculum_records, output_root, netuid=netuid, resolver=resolver)
        if curriculum_records
        else {}
    )
    return {**active_pool, **accepted, **curriculum}


def _write_cid_bound_commitment(
    artifacts: dict[str, object],
    *,
    active_pool_cid: str,
    accepted_cid: str,
) -> str:
    from lemma.chain.commitments import compact_tempo_cid_commitment_payload
    from lemma.corpus.storage import canonical_json_bytes

    commitment_path = artifacts.get("commitment")
    if not isinstance(commitment_path, Path):
        raise RuntimeError("missing tempo commitment path")
    payload = json.loads(commitment_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"{commitment_path}: expected JSON object")
    tempo_payload = compact_tempo_cid_commitment_payload(
        netuid=payload["netuid"],
        tempo=payload["tempo"],
        active_pool_directory_cid=active_pool_cid,
        active_pool_directory_sha256=str(payload["active_pool_directory_sha256"]),
        accepted_directory_cid=accepted_cid,
        accepted_directory_sha256=str(payload["tempo_directory_sha256"]),
        accepted_merkle_root=str(payload["accepted_merkle_root"]),
    )
    payload["active_pool_directory_cid"] = active_pool_cid
    payload["tempo_directory_cid"] = accepted_cid
    payload["tempo_commitment_payload"] = tempo_payload
    payload["commitment_payload"] = tempo_payload
    commitment_path.write_bytes(canonical_json_bytes(payload))
    artifacts["active_pool_directory_cid"] = active_pool_cid
    artifacts["tempo_directory_cid"] = accepted_cid
    artifacts["tempo_commitment_payload"] = tempo_payload
    return tempo_payload


def _publish_public_tempo_artifacts(
    settings: LemmaSettings,
    artifacts: dict[str, object],
) -> tuple[dict[str, str], ...]:
    if not settings.canonical_publish_s3_uri.strip() and not settings.canonical_publish_ipfs_api_url.strip():
        return ()
    from lemma.corpus.publish import add_directory_to_ipfs, aws_command, publish_paths_to_s3

    output_root = _canonical_output_root(settings)
    rows: list[dict[str, str]] = []
    active_pool_directory = artifacts.get("active_pool_directory")
    accepted_directory = artifacts.get("directory")
    curriculum_directory = artifacts.get("curriculum_directory")
    if settings.canonical_publish_ipfs_api_url.strip():
        if not isinstance(active_pool_directory, Path) or not isinstance(accepted_directory, Path):
            raise RuntimeError("missing canonical directories for IPFS publish")
        active_ipfs = add_directory_to_ipfs(
            active_pool_directory,
            api_url=settings.canonical_publish_ipfs_api_url,
            verify=settings.canonical_publish_verify,
            timeout_s=settings.canonical_publish_ipfs_timeout_s,
        )
        accepted_ipfs = add_directory_to_ipfs(
            accepted_directory,
            api_url=settings.canonical_publish_ipfs_api_url,
            verify=settings.canonical_publish_verify,
            timeout_s=settings.canonical_publish_ipfs_timeout_s,
        )
        _write_cid_bound_commitment(
            artifacts,
            active_pool_cid=active_ipfs.cid,
            accepted_cid=accepted_ipfs.cid,
        )
        rows.extend(
            [
                {
                    "kind": "ipfs_directory",
                    "local_path": str(Path(active_ipfs.path).relative_to(output_root)),
                    "cid": active_ipfs.cid,
                    "file_count": str(active_ipfs.file_count),
                },
                {
                    "kind": "ipfs_directory",
                    "local_path": str(Path(accepted_ipfs.path).relative_to(output_root)),
                    "cid": accepted_ipfs.cid,
                    "file_count": str(accepted_ipfs.file_count),
                },
            ]
        )
        if isinstance(curriculum_directory, Path):
            curriculum_ipfs = add_directory_to_ipfs(
                curriculum_directory,
                api_url=settings.canonical_publish_ipfs_api_url,
                verify=settings.canonical_publish_verify,
                timeout_s=settings.canonical_publish_ipfs_timeout_s,
            )
            rows.append(
                {
                    "kind": "ipfs_directory",
                    "local_path": str(Path(curriculum_ipfs.path).relative_to(output_root)),
                    "cid": curriculum_ipfs.cid,
                    "file_count": str(curriculum_ipfs.file_count),
                }
            )
    if not settings.canonical_publish_s3_uri.strip():
        append_jsonl(settings.operator_data_dir / "canonical-publish.jsonl", rows)
        return tuple(rows)

    paths = tuple(
        path
        for path in (
            active_pool_directory,
            accepted_directory,
            curriculum_directory,
            artifacts.get("commitment"),
        )
        if isinstance(path, Path)
    )
    published = publish_paths_to_s3(
        paths,
        root=output_root,
        s3_uri=settings.canonical_publish_s3_uri,
        endpoint_url=settings.canonical_publish_endpoint_url,
        aws=aws_command(settings.canonical_publish_aws_command),
        verify=settings.canonical_publish_verify,
    )
    rows.extend(
        {
            "kind": "s3_object",
            "local_path": item.local_path,
            "s3_uri": item.s3_uri,
            "sha256": item.sha256,
        }
        for item in published
    )
    append_jsonl(settings.operator_data_dir / "canonical-publish.jsonl", rows)
    return tuple(rows)


def _require_cid_publish_for_production_commitment(settings: LemmaSettings) -> None:
    if (
        settings.protocol_mode == "production"
        and settings.enable_set_commitment
        and not settings.canonical_publish_ipfs_api_url.strip()
    ):
        raise RuntimeError("production tempo chain commitments require LEMMA_CANONICAL_PUBLISH_IPFS_API_URL")


def _active_slot_weights(settings: LemmaSettings, active_tasks: Sequence[LemmaTask]) -> dict[str, float]:
    if settings.protocol_mode != "production" or settings.procedural_import_graph_jsonl is None:
        return {task.id: task_slot_weight(task) for task in active_tasks}
    from lemma.supply.import_graph import read_import_graph

    import_graph = read_import_graph(settings.procedural_import_graph_jsonl)
    return {task.id: slot_weight_receipt_for_task(task, import_graph=import_graph).weight for task in active_tasks}


def _verified_slot_weights(
    settings: LemmaSettings,
    active_tasks: Sequence[LemmaTask],
    records: Sequence[VerificationRecord],
    accepted: dict[tuple[str, str, str], tuple[LemmaTask, LemmaSubmission, VerifyResult]],
    *,
    require_strong_identity: bool,
) -> dict[str, float]:
    weights = _active_slot_weights(settings, active_tasks)
    active_ids = {task.id for task in active_tasks}
    task_by_id = {task.id: task for task in active_tasks}
    for task_id in active_ids:
        task_records = sorted(
            (record for record in records if record.task_id == task_id and record.passed),
            key=_record_rank_key,
        )
        for record in task_records:
            if not record.reward_eligible:
                continue
            if require_strong_identity and record.proof_identity_strength != "strong":
                continue
            accepted_entry = accepted.get((record.task_id, record.solver_hotkey, record.proof_sha256))
            if accepted_entry is None:
                continue
            result = accepted_entry[2]
            if result.kernel_dependencies:
                weights[task_id] = slot_weight_receipt_for_kernel_dependencies(
                    task_by_id[task_id],
                    kernel_dependencies=result.kernel_dependencies,
                ).weight
            break
    return weights


def _task_with_verified_slot_weight(task: LemmaTask, result: VerifyResult) -> LemmaTask:
    if not result.kernel_dependencies:
        return task
    receipt = slot_weight_receipt_for_kernel_dependencies(task, kernel_dependencies=result.kernel_dependencies)
    return task.model_copy(update={"metadata": {**task.metadata, **receipt.metadata()}})


def _record_rank_key(record: VerificationRecord) -> tuple[object, ...]:
    if record.commit_block is not None:
        tie_break = record.proof_identity or record.proof_term_hash or record.proof_sha256
        return (0, record.commit_block, tie_break, record.solver_hotkey, record.received_at)
    return (1, record.received_at)


def _retarget_curriculum_after_validation(settings: LemmaSettings, *, tempo: int, solved_slots: int):
    if not settings.curriculum_retarget_enabled or settings.curriculum_state_jsonl is None:
        return None
    if settings.curriculum_k_max < settings.curriculum_k_min:
        raise RuntimeError("LEMMA_CURRICULUM_K_MAX must be >= LEMMA_CURRICULUM_K_MIN")

    from lemma.supply.controller import (
        CurriculumConfig,
        CurriculumState,
        CurriculumTempoRecord,
        append_curriculum_record,
        curriculum_retarget_receipt,
        read_curriculum_records,
        retarget_curriculum,
    )

    records = read_curriculum_records(settings.curriculum_state_jsonl)
    for record in records:
        if record.tempo == tempo:
            return record
    prior_ema = records[-1].ema_solve_rate if records else 0.50
    active_k = settings.active_task_count
    validator_capacity = settings.validator_capacity or active_k
    config = CurriculumConfig(
        beta=settings.curriculum_beta,
        low_band=settings.curriculum_low_band,
        high_band=settings.curriculum_high_band,
        k_min=settings.curriculum_k_min,
        k_max=settings.curriculum_k_max,
        cost_budget_s=settings.curriculum_cost_budget_s,
        base_task_cost_s=settings.curriculum_base_task_cost_s,
        depth_cost_multiplier=settings.curriculum_depth_cost_multiplier,
    )
    previous_state = CurriculumState(
        active_K=active_k,
        frontier_depth=settings.frontier_depth,
        ema_solve_rate=prior_ema,
    )
    decision = retarget_curriculum(
        previous_state,
        solved_slots=solved_slots,
        validator_capacity=validator_capacity,
        config=config,
    )
    record = CurriculumTempoRecord(
        tempo=tempo,
        active_K=decision.state.active_K,
        frontier_depth=decision.state.frontier_depth,
        ema_solve_rate=decision.state.ema_solve_rate,
        solved_slots=solved_slots,
        parked_task_ids=(),
        action=decision.action,
        variant_stream_requested=decision.variant_stream_requested,
        retarget_receipt=curriculum_retarget_receipt(
            tempo=tempo,
            previous_state=previous_state,
            solved_slots=solved_slots,
            validator_capacity=validator_capacity,
            config=config,
            decision=decision,
        ),
    )
    append_curriculum_record(settings.curriculum_state_jsonl, record)
    return record


def validate_once(
    settings: LemmaSettings,
    submissions: Iterable[LemmaSubmission],
    *,
    registry: TaskRegistry | None = None,
    verify_submission: VerifySubmission | None = None,
    validator_hotkey: str | None = None,
    epoch: int | None = None,
    tempo: int | None = None,
    no_set_weights: bool = False,
    require_signatures: bool = False,
    require_commit_reveal: bool = False,
    submit_weights: SubmitWeights | None = None,
    submit_commitment: SubmitCommitment | None = None,
    chain_authenticated_keys: frozenset[ChainAuthenticatedKey] = frozenset(),
) -> ValidatorRunResult:
    """Verify submissions, score unique proofs, and write local corpus artifacts."""
    active_tempo = current_active_tempo(settings) if tempo is None else tempo
    settings = curriculum_controlled_settings(settings, tempo=active_tempo)
    registry = registry or task_registry_for_validation(settings, tempo=active_tempo)
    enforce_production_invariants(settings, registry)
    active_tasks = active_tasks_for_validation(registry, settings, tempo=active_tempo)
    tasks = {task.id: task for task in active_tasks}
    verify = verify_submission or _default_verify(settings)
    validator = validator_hotkey or settings.wallet_hot
    require_live_signatures = (
        require_signatures or settings.require_submission_signatures or settings.protocol_mode == "production"
    )
    require_reveal = require_commit_reveal or settings.require_commit_reveal or settings.protocol_mode == "production"
    require_strong_identity = settings.require_strong_proof_identity or settings.protocol_mode == "production"

    records: list[VerificationRecord] = []
    accepted: dict[tuple[str, str, str], tuple[LemmaTask, LemmaSubmission, VerifyResult]] = {}
    receipts: list[dict[str, object]] = []

    for submission in submissions:
        received_at = _now()
        task = tasks.get(submission.task_id)
        if task is None:
            receipts.append(
                {
                    "received_at": received_at,
                    "task_id": submission.task_id,
                    "accepted": False,
                    "reason": "inactive_task",
                }
            )
            continue
        chain_authenticated = (task.id, submission.solver_hotkey, submission.proof_sha256) in chain_authenticated_keys
        if settings.protocol_mode == "production" and not chain_authenticated:
            receipts.append(
                {
                    "received_at": received_at,
                    "task_id": task.id,
                    "task_version": task.task_version,
                    "target_sha256": task.target_sha256,
                    "solver_hotkey": submission.solver_hotkey,
                    "proof_sha256": submission.proof_sha256,
                    "accepted": False,
                    "reason": "production_requires_bucket_chain_authentication",
                }
            )
            continue
        try:
            validate_submission_for_task(
                submission,
                task,
                require_signature=require_live_signatures and not chain_authenticated,
                require_commit_reveal=require_reveal,
            )
        except ValueError as e:
            receipts.append(
                {
                    "received_at": received_at,
                    "task_id": submission.task_id,
                    "accepted": False,
                    "reason": str(e),
                }
            )
            continue

        result = verify(task, submission)
        identity = proof_identity(
            proof_sha256=submission.proof_sha256,
            proof_term_hash=result.proof_term_hash,
            structural_fingerprint=result.structural_fingerprint,
            proof_script=submission.proof_script,
        )
        eligibility = task_reward_eligibility(task)
        record = VerificationRecord(
            task_id=task.id,
            task_version=task.task_version,
            target_sha256=task.target_sha256,
            solver_hotkey=submission.solver_hotkey,
            validator_hotkey=validator,
            passed=result.passed,
            reason=result.reason,
            proof_sha256=submission.proof_sha256,
            proof_identity=identity.value,
            proof_term_hash=identity.proof_term_hash,
            proof_identity_source=identity.source,
            proof_identity_strength=identity.strength,
            reward_eligible=eligibility.eligible,
            reward_ineligibility_reason=eligibility.reason,
            commit_block=submission.commit_block,
            drand_round=submission.drand_round,
            received_at=received_at,
        )
        records.append(record)
        receipts.append(
            {
                "received_at": received_at,
                "task_id": task.id,
                "task_version": task.task_version,
                "target_sha256": task.target_sha256,
                "solver_hotkey": submission.solver_hotkey,
                "proof_sha256": submission.proof_sha256,
                "commit_block": submission.commit_block,
                "drand_round": submission.drand_round,
                "chain_authenticated": chain_authenticated,
                "passed": result.passed,
                "reason": result.reason,
            }
        )
        if result.passed:
            accepted[(task.id, submission.solver_hotkey, submission.proof_sha256)] = (task, submission, result)

    append_jsonl(settings.operator_data_dir / "verification-records.jsonl", receipts)

    score = score_epoch(
        records,
        active_task_count=len(active_tasks),
        unearned_policy=settings.unearned_allocation_policy,
        unearned_uid=settings.unearned_uid,
        require_strong_identity_for_reward=require_strong_identity,
        slot_weights=_verified_slot_weights(
            settings,
            active_tasks,
            records,
            accepted,
            require_strong_identity=require_strong_identity,
        ),
    )
    if score.score_events:
        append_jsonl(settings.operator_data_dir / "score-events.jsonl", score.score_events)
    _retarget_curriculum_after_validation(
        settings,
        tempo=active_tempo,
        solved_slots=len(score.valid_unique_proofs),
    )
    curriculum_records: tuple[CurriculumTempoRecord, ...] = ()
    if settings.curriculum_retarget_enabled and settings.curriculum_state_jsonl is not None:
        from lemma.supply.controller import read_curriculum_records

        curriculum_records = read_curriculum_records(settings.curriculum_state_jsonl)
    rows: list[CorpusRow] = []
    for scored in score.valid_unique_proofs:
        key = (scored.record.task_id, scored.record.solver_hotkey, scored.record.proof_sha256)
        task, submission, result = accepted[key]
        task = _task_with_verified_slot_weight(task, result)
        rows.append(
            build_corpus_row(
                task,
                submission,
                result,
                validator_hotkey=validator,
                rewarded=scored.rewarded,
                epoch=epoch,
                tempo=active_tempo,
                proof_term_hash=scored.record.proof_term_hash,
                structural_fingerprint=result.structural_fingerprint,
                proof_identity_source=scored.record.proof_identity_source,
                active_K=len(active_tasks),
                accepted_at=scored.record.received_at,
            )
        )

    existing_row_ids = _existing_corpus_row_ids(settings.corpus_output_dir, tempo=active_tempo)
    new_rows = [row for row in rows if row.row_id not in existing_row_ids]
    if new_rows:
        output_path = (
            settings.corpus_output_dir / f"epoch-{epoch}.jsonl"
            if epoch is not None
            else _next_local_epoch_file(settings.corpus_output_dir)
        )
        write_jsonl(new_rows, output_path)
        write_corpus_index(settings.corpus_output_dir, settings.corpus_output_dir / "corpus-index.json")

    public_artifacts = _write_public_tempo_artifacts(
        settings,
        active_tasks=active_tasks,
        rows=rows,
        tempo=active_tempo,
        curriculum_records=curriculum_records,
    )
    _require_cid_publish_for_production_commitment(settings)
    published_artifacts = _publish_public_tempo_artifacts(settings, public_artifacts)

    weights_set = False
    weight_submission: ChainWeightSubmission | None = None
    if not no_set_weights and settings.enable_set_weights:
        from lemma.chain.weights import submit_bittensor_weights

        writer = submit_weights or submit_bittensor_weights
        weight_submission = writer(settings, score.weights)
        append_jsonl(
            settings.operator_data_dir / "weight-submissions.jsonl",
            [
                _weight_receipt(
                    submitted_at=_now(),
                    settings=settings,
                    registry_sha256=registry.sha256,
                    submission=weight_submission,
                )
            ],
        )
        if not weight_submission.success:
            message = weight_submission.message or "unknown set_weights failure"
            raise RuntimeError(f"set_weights failed: {message}")
        weights_set = True
    commitment_set = False
    commitment_submission: ChainCommitmentSubmission | None = None
    tempo_commitment_payload = str(public_artifacts.get("tempo_commitment_payload") or "")
    if settings.enable_set_commitment:
        from lemma.chain.commitments import submit_storage_commitment

        writer_commitment = submit_commitment or submit_storage_commitment
        commitment_submission = writer_commitment(settings, tempo_commitment_payload)
        append_jsonl(
            settings.operator_data_dir / "commitment-submissions.jsonl",
            [
                _commitment_receipt(
                    submitted_at=_now(),
                    settings=settings,
                    registry_sha256=registry.sha256,
                    submission=commitment_submission,
                )
            ],
        )
        if not commitment_submission.success:
            message = commitment_submission.message or "unknown set_commitment failure"
            raise RuntimeError(f"set_commitment failed: {message}")
        commitment_set = True
    summary = ValidatorRunSummary(
        schema_version=1,
        run_at=_now(),
        registry_sha256=registry.sha256,
        active_K=len(active_tasks),
        active_tempo=active_tempo,
        active_seed_mode=settings.active_seed_mode,
        active_epoch_randomness_source=settings.active_epoch_randomness_source,
        active_epoch_randomness_sha256=active_epoch_randomness_sha256(settings, tempo=active_tempo),
        active_selection_seed_sha256=active_selection_seed_sha256(registry, settings, tempo=active_tempo),
        active_task_ids=tuple(task.id for task in active_tasks),
        active_pool_directory_sha256=str(public_artifacts.get("active_pool_directory_sha256") or "") or None,
        active_pool_merkle_root=str(public_artifacts.get("active_pool_merkle_root") or "") or None,
        accepted_merkle_root=str(public_artifacts.get("accepted_merkle_root") or "") or None,
        accepted_directory_sha256=str(public_artifacts.get("tempo_directory_sha256") or "") or None,
        tempo_commitment_payload=tempo_commitment_payload,
        chain_commitment_set=commitment_set,
        canonical_publish_uri=settings.canonical_publish_s3_uri,
        canonical_publish_count=len(published_artifacts),
        frontier_depth=settings.frontier_depth,
        verified_count=len(records),
        accepted_unique_count=len(score.valid_unique_proofs),
        rewarded_count=sum(1 for event in score.score_events if event.rewarded),
        score_event_count=len(score.score_events),
        corpus_row_count=len(rows),
        unearned_share=score.unearned_share,
        unearned_policy=score.unearned_policy,
        weights_set=weights_set,
        chain_weight_uids=weight_submission.uids if weight_submission else None,
        chain_weight_values=weight_submission.weights if weight_submission else None,
    )
    append_jsonl(settings.operator_data_dir / "validator-runs.jsonl", [summary])
    return ValidatorRunResult(
        verification_records=tuple(records),
        score=score,
        corpus_rows=tuple(rows),
        weights_set=weights_set,
        weight_submission=weight_submission,
        commitment_submission=commitment_submission,
        summary=summary,
    )


def submissions_from_json_array(raw: str) -> list[LemmaSubmission]:
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError("submission payload must be a JSON array")
    return [LemmaSubmission.model_validate(item) for item in payload]
