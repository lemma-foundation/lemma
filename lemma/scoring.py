"""First-accepted unique proof scoring for training tasks."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


class VerificationResult(BaseModel):
    """Replayable validator result for one task-bound submission."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    task_id: str
    task_version: int = Field(default=1, ge=1)
    target_sha256: str = ""
    solver_hotkey: str
    validator_hotkey: str = ""
    passed: bool
    reason: str = ""
    proof_sha256: str
    proof_term_hash: str | None = None
    received_at: str = ""
    verifier_version: str = "lemma-lean-v1"

    @property
    def proof_identity(self) -> str:
        return self.proof_term_hash or self.proof_sha256


VerificationRecord = VerificationResult


class ScoreEvent(BaseModel):
    """Deterministic score emitted from a verified unique proof."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    task_id: str
    task_version: int = Field(default=1, ge=1)
    target_sha256: str
    solver_hotkey: str
    validator_hotkey: str = ""
    proof_identity: str
    proof_sha256: str
    proof_term_hash: str | None = None
    rewarded: bool
    credit: int
    score: float
    received_at: str = ""


@dataclass(frozen=True)
class ScoredProof:
    record: VerificationRecord
    proof_identity: str
    rewarded: bool


@dataclass(frozen=True)
class ScoreResult:
    winners: dict[str, str]
    credits: dict[str, int]
    scores: dict[str, float]
    weights: dict[str, float]
    score_events: tuple[ScoreEvent, ...] = ()
    valid_unique_proofs: tuple[ScoredProof, ...] = ()


def score_epoch(records: list[VerificationRecord], *, active_task_count: int | None = None) -> ScoreResult:
    """Award one credit to the first accepted unique proof for each task."""
    if active_task_count is not None and active_task_count < 0:
        raise ValueError("active_task_count must be non-negative")

    by_task: dict[str, list[tuple[int, VerificationRecord]]] = defaultdict(list)
    for index, record in enumerate(records):
        if record.passed:
            by_task[record.task_id].append((index, record))

    winners: dict[str, str] = {}
    scored: list[ScoredProof] = []
    score_events: list[ScoreEvent] = []
    task_count = active_task_count if active_task_count is not None else len({record.task_id for record in records})
    for task_id, task_records in by_task.items():
        ordered = sorted(task_records, key=lambda item: (item[1].received_at, item[0]))
        seen: set[str] = set()
        rewarded_this_task = False
        for _, record in ordered:
            if record.proof_identity in seen:
                continue
            seen.add(record.proof_identity)
            rewarded = not rewarded_this_task
            if rewarded:
                winners[task_id] = record.solver_hotkey
                rewarded_this_task = True
            scored.append(ScoredProof(record=record, proof_identity=record.proof_identity, rewarded=rewarded))
            score_events.append(
                ScoreEvent(
                    task_id=record.task_id,
                    task_version=record.task_version,
                    target_sha256=record.target_sha256,
                    solver_hotkey=record.solver_hotkey,
                    validator_hotkey=record.validator_hotkey,
                    proof_identity=record.proof_identity,
                    proof_sha256=record.proof_sha256,
                    proof_term_hash=record.proof_term_hash,
                    rewarded=rewarded,
                    credit=1 if rewarded else 0,
                    score=(1 / task_count) if rewarded and task_count else 0.0,
                    received_at=record.received_at,
                )
            )

    credits = dict(Counter(winners.values()))
    scores = {hotkey: credit / task_count for hotkey, credit in credits.items()} if task_count else {}
    total = sum(credits.values())
    weights = {hotkey: credit / total for hotkey, credit in credits.items()} if total else {}
    return ScoreResult(
        winners=winners,
        credits=credits,
        scores=scores,
        weights=weights,
        score_events=tuple(score_events),
        valid_unique_proofs=tuple(scored),
    )
