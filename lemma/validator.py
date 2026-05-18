"""Validator-side verification, scoring, and corpus writing."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from lemma.common.config import LemmaSettings
from lemma.corpus import CorpusRow, build_corpus_row, write_corpus_index, write_jsonl
from lemma.lean.proof_identity import proof_identity
from lemma.lean.sandbox import VerifyResult
from lemma.lean.verify_runner import run_lean_verify
from lemma.scoring import ScoreResult, VerificationRecord, score_epoch
from lemma.store import append_jsonl
from lemma.submissions import LemmaSubmission, validate_submission_for_task
from lemma.tasks import LemmaTask, TaskRegistry, fetch_task_registry

VerifySubmission = Callable[[LemmaTask, LemmaSubmission], VerifyResult]


@dataclass(frozen=True)
class ValidatorRunResult:
    verification_records: tuple[VerificationRecord, ...]
    score: ScoreResult
    corpus_rows: tuple[CorpusRow, ...]
    weights_set: bool


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_verify(settings: LemmaSettings) -> VerifySubmission:
    def verify(task: LemmaTask, submission: LemmaSubmission) -> VerifyResult:
        return run_lean_verify(
            settings,
            verify_timeout_s=settings.lean_verify_timeout_s,
            problem=task.to_problem(),
            proof_script=submission.proof_script,
            submission_policy=task.policy,
        )

    return verify


def read_submissions_jsonl(path: Path) -> list[LemmaSubmission]:
    rows: list[LemmaSubmission] = []
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(LemmaSubmission.model_validate_json(line))
        except ValueError as e:
            raise ValueError(f"{path}:{no}: invalid submission: {e}") from e
    return rows


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
) -> ValidatorRunResult:
    """Verify submissions, score unique proofs, and write local corpus artifacts."""
    registry = registry or fetch_task_registry(settings)
    tasks = {task.id: task for task in registry.tasks}
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
        identity = proof_identity(proof_sha256=submission.proof_sha256, proof_term_hash=result.proof_term_hash)
        record = VerificationRecord(
            task_id=task.id,
            task_version=task.task_version,
            target_sha256=task.target_sha256,
            solver_hotkey=submission.solver_hotkey,
            validator_hotkey=validator,
            passed=result.passed,
            reason=result.reason,
            proof_sha256=submission.proof_sha256,
            proof_term_hash=identity.proof_term_hash,
            proof_identity_source=identity.source,
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
        active_task_count=len(tasks),
        unearned_policy=settings.unearned_allocation_policy,
        unearned_uid=settings.unearned_uid,
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
                active_K=len(tasks),
                accepted_at=scored.record.received_at,
            )
        )

    if rows:
        filename = f"epoch-{epoch}.jsonl" if epoch is not None else "epoch-local.jsonl"
        write_jsonl(rows, settings.corpus_output_dir / filename)
        write_corpus_index(settings.corpus_output_dir, settings.corpus_output_dir / "corpus-index.json")

    weights_set = bool(score.weights) and not no_set_weights
    return ValidatorRunResult(
        verification_records=tuple(records),
        score=score,
        corpus_rows=tuple(rows),
        weights_set=weights_set,
    )


def submissions_from_json_array(raw: str) -> list[LemmaSubmission]:
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError("submission payload must be a JSON array")
    return [LemmaSubmission.model_validate(item) for item in payload]
