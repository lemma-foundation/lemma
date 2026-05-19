"""Validator-side verification, scoring, and corpus writing."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lemma.common.config import LemmaSettings
from lemma.corpus import CorpusRow, build_corpus_row, write_corpus_index, write_jsonl
from lemma.lean.proof_identity import proof_identity
from lemma.lean.sandbox import VerifyResult
from lemma.protocol_invariants import enforce_production_invariants
from lemma.scoring import ScoreResult, UnearnedPolicy, VerificationRecord, score_epoch
from lemma.store import append_jsonl
from lemma.submissions import LemmaSubmission, validate_submission_for_task
from lemma.supply.queue import initial_active_pool
from lemma.task_activation import task_reward_eligibility
from lemma.tasks import LemmaTask, TaskRegistry, fetch_task_registry
from lemma.verifiers.lean import verify_result_from_adapter_result
from lemma.verifiers.registry import get_verifier

VerifySubmission = Callable[[LemmaTask, LemmaSubmission], VerifyResult]
SubmitWeights = Callable[[LemmaSettings, dict[str, float]], bool]


class ValidatorRunSummary(BaseModel):
    """Public-safe summary of one validator pass."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    run_at: str
    registry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    active_K: int = Field(ge=0)
    frontier_depth: int = Field(ge=0)
    verified_count: int = Field(ge=0)
    accepted_unique_count: int = Field(ge=0)
    rewarded_count: int = Field(ge=0)
    score_event_count: int = Field(ge=0)
    corpus_row_count: int = Field(ge=0)
    unearned_share: float = Field(ge=0.0, le=1.0)
    unearned_policy: UnearnedPolicy
    weights_set: bool


@dataclass(frozen=True)
class ValidatorRunResult:
    verification_records: tuple[VerificationRecord, ...]
    score: ScoreResult
    corpus_rows: tuple[CorpusRow, ...]
    weights_set: bool
    summary: ValidatorRunSummary


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    candidates = tuple(task for task in registry.tasks if task.queue_depth <= settings.frontier_depth)
    active_k = min(settings.active_task_count, len(candidates))
    if active_k == 0:
        return ()
    pool = initial_active_pool(
        candidates,
        active_K=active_k,
        tempo=tempo or 0,
        seed=settings.active_queue_seed,
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
    submit_weights: SubmitWeights | None = None,
) -> ValidatorRunResult:
    """Verify submissions, score unique proofs, and write local corpus artifacts."""
    registry = registry or fetch_task_registry(settings)
    enforce_production_invariants(settings, registry)
    active_tasks = active_tasks_for_validation(registry, settings, tempo=tempo)
    tasks = {task.id: task for task in active_tasks}
    verify = verify_submission or _default_verify(settings)
    validator = validator_hotkey or settings.wallet_hot

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
        try:
            validate_submission_for_task(submission, task, require_signature=require_signatures)
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
        require_strong_identity_for_reward=settings.protocol_mode == "production",
    )
    if score.score_events:
        append_jsonl(settings.operator_data_dir / "score-events.jsonl", score.score_events)
    rows: list[CorpusRow] = []
    for scored in score.valid_unique_proofs:
        key = (scored.record.task_id, scored.record.solver_hotkey, scored.record.proof_sha256)
        task, submission, result = accepted[key]
        rows.append(
            build_corpus_row(
                task,
                submission,
                result,
                validator_hotkey=validator,
                rewarded=scored.rewarded,
                epoch=epoch,
                tempo=tempo,
                proof_term_hash=scored.record.proof_term_hash,
                proof_identity_source=scored.record.proof_identity_source,
                active_K=len(active_tasks),
                accepted_at=scored.record.received_at,
            )
        )

    if rows:
        output_path = (
            settings.corpus_output_dir / f"epoch-{epoch}.jsonl"
            if epoch is not None
            else _next_local_epoch_file(settings.corpus_output_dir)
        )
        write_jsonl(rows, output_path)
        write_corpus_index(settings.corpus_output_dir, settings.corpus_output_dir / "corpus-index.json")

    weights_set = False
    if not no_set_weights and settings.enable_set_weights:
        from lemma.chain.weights import submit_bittensor_weights

        writer = submit_weights or submit_bittensor_weights
        weights_set = writer(settings, score.weights)
    summary = ValidatorRunSummary(
        schema_version=1,
        run_at=_now(),
        registry_sha256=registry.sha256,
        active_K=len(active_tasks),
        frontier_depth=settings.frontier_depth,
        verified_count=len(records),
        accepted_unique_count=len(score.valid_unique_proofs),
        rewarded_count=sum(1 for event in score.score_events if event.rewarded),
        score_event_count=len(score.score_events),
        corpus_row_count=len(rows),
        unearned_share=score.unearned_share,
        unearned_policy=score.unearned_policy,
        weights_set=weights_set,
    )
    append_jsonl(settings.operator_data_dir / "validator-runs.jsonl", [summary])
    return ValidatorRunResult(
        verification_records=tuple(records),
        score=score,
        corpus_rows=tuple(rows),
        weights_set=weights_set,
        summary=summary,
    )


def submissions_from_json_array(raw: str) -> list[LemmaSubmission]:
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError("submission payload must be a JSON array")
    return [LemmaSubmission.model_validate(item) for item in payload]
