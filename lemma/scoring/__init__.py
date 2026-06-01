"""First-accepted unique proof scoring for training tasks."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lemma.lean.proof_identity import full_reward_eligible, identity_strength

UnearnedPolicy = Literal["burn", "recycle", "hold"]


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
    proof_identity: str = ""
    proof_identity_source: str = "script_sha256"
    proof_identity_strength: Literal["weak", "medium", "strong"] = "weak"
    reward_eligible: bool = True
    reward_ineligibility_reason: str = ""
    commit_block: int | None = Field(default=None, ge=0)
    commit_extrinsic_index: int | None = Field(default=None, ge=0)
    commit_event_index: int | None = Field(default=None, ge=0)
    commit_extrinsic_hash: str | None = None
    drand_round: int | None = Field(default=None, ge=0)
    received_at: str = ""
    verifier_version: str = "lemma-lean-v1"

    @model_validator(mode="after")
    def _fill_identity(self) -> VerificationResult:
        if not self.proof_identity:
            self.proof_identity = self.proof_term_hash or self.proof_sha256
        self.proof_identity_strength = identity_strength(self.proof_identity_source)
        return self


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
    proof_identity_source: str = "script_sha256"
    proof_identity_strength: Literal["weak", "medium", "strong"] = "weak"
    full_reward_eligible: bool = False
    reward_eligible: bool = True
    reward_ineligibility_reason: str = ""
    rewarded: bool
    credit: int
    score: float
    active_K: int = Field(ge=0)
    commit_block: int | None = Field(default=None, ge=0)
    commit_extrinsic_index: int | None = Field(default=None, ge=0)
    commit_event_index: int | None = Field(default=None, ge=0)
    commit_extrinsic_hash: str | None = None
    drand_round: int | None = Field(default=None, ge=0)
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
    miner_weights: dict[str, float]
    weights: dict[str, float]
    unearned_policy: UnearnedPolicy
    unearned_share: float
    unearned_uid: int | None
    score_events: tuple[ScoreEvent, ...] = ()
    valid_unique_proofs: tuple[ScoredProof, ...] = ()


def score_epoch(
    records: list[VerificationRecord],
    *,
    active_task_count: int | None = None,
    unearned_policy: UnearnedPolicy = "burn",
    unearned_uid: int | None = 0,
    require_strong_identity_for_reward: bool = False,
    slot_weights: Mapping[str, float] | None = None,
) -> ScoreResult:
    """Award rank-0 valid proof credit without redistributing unsolved slots.

    With no slot weights, each active slot is worth ``1 / K``. When slot
    weights are supplied, each slot is worth its normalized share of the active
    weight sum. Committed reveals rank by chain commit block, extrinsic index,
    event index, then commitment hash before local receipt time. Either way,
    unsolved-slot value is tracked separately.
    """
    if active_task_count is not None and active_task_count < 0:
        raise ValueError("active_task_count must be non-negative")
    if unearned_uid is not None and unearned_uid < 0:
        raise ValueError("unearned_uid must be non-negative")

    by_task: dict[str, list[tuple[int, VerificationRecord]]] = defaultdict(list)
    for index, record in enumerate(records):
        if record.passed:
            by_task[record.task_id].append((index, record))

    winners: dict[str, str] = {}
    scored: list[ScoredProof] = []
    score_events: list[ScoreEvent] = []
    task_count = active_task_count if active_task_count is not None else len({record.task_id for record in records})
    slot_shares = _slot_shares(slot_weights, task_count)
    for task_id, task_records in by_task.items():
        if slot_shares is not None and task_id not in slot_shares:
            raise ValueError(f"missing slot weight for task {task_id}")
        ordered = sorted(task_records, key=_record_rank_key)
        seen: set[str] = set()
        rewarded_this_task = False
        for _, record in ordered:
            if record.proof_identity in seen:
                continue
            seen.add(record.proof_identity)
            identity_ok = full_reward_eligible(record.proof_identity_strength)
            eligible = record.reward_eligible and (identity_ok or not require_strong_identity_for_reward)
            rewarded = eligible and not rewarded_this_task
            if rewarded:
                winners[task_id] = record.solver_hotkey
                rewarded_this_task = True
            reason = record.reward_ineligibility_reason
            if require_strong_identity_for_reward and not identity_ok:
                reason = reason or "weak_proof_identity"
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
                    proof_identity_source=record.proof_identity_source,
                    proof_identity_strength=record.proof_identity_strength,
                    full_reward_eligible=identity_ok and record.reward_eligible,
                    reward_eligible=record.reward_eligible,
                    reward_ineligibility_reason=reason,
                    rewarded=rewarded,
                    credit=1 if rewarded else 0,
                    score=_task_share(task_id, task_count, slot_shares) if rewarded else 0.0,
                    active_K=task_count,
                    commit_block=record.commit_block,
                    commit_extrinsic_index=record.commit_extrinsic_index,
                    commit_event_index=record.commit_event_index,
                    commit_extrinsic_hash=record.commit_extrinsic_hash,
                    drand_round=record.drand_round,
                    received_at=record.received_at,
                )
            )

    credits = dict(Counter(winners.values()))
    if slot_shares is None:
        scores = {hotkey: credit / task_count for hotkey, credit in credits.items()} if task_count else {}
    else:
        weighted_scores: dict[str, float] = defaultdict(float)
        for event in score_events:
            if event.rewarded:
                weighted_scores[event.solver_hotkey] += event.score
        scores = dict(weighted_scores)
    miner_weights = dict(scores)
    solved_share = min(1.0, sum(miner_weights.values()))
    unearned_share = (1.0 - solved_share) if task_count else 0.0
    weights = dict(miner_weights)
    if unearned_share and unearned_uid is not None:
        weights[f"{unearned_policy}_uid:{unearned_uid}"] = unearned_share
    return ScoreResult(
        winners=winners,
        credits=credits,
        scores=scores,
        miner_weights=miner_weights,
        weights=weights,
        unearned_policy=unearned_policy,
        unearned_share=unearned_share,
        unearned_uid=unearned_uid,
        score_events=tuple(score_events),
        valid_unique_proofs=tuple(scored),
    )


def _slot_shares(slot_weights: Mapping[str, float] | None, task_count: int) -> dict[str, float] | None:
    if slot_weights is None:
        return None
    cleaned: dict[str, float] = {}
    for task_id, raw in slot_weights.items():
        weight = float(raw)
        if weight <= 0:
            raise ValueError(f"slot weight must be positive for task {task_id}")
        cleaned[task_id] = weight
    total = sum(cleaned.values())
    if task_count and len(cleaned) != task_count:
        raise ValueError("slot_weights must cover every active task")
    if not total:
        if task_count == 0:
            return {}
        raise ValueError("slot_weights must contain a positive total")
    return {task_id: weight / total for task_id, weight in cleaned.items()}


def _task_share(task_id: str, task_count: int, slot_shares: dict[str, float] | None) -> float:
    if slot_shares is not None:
        return slot_shares[task_id]
    return (1 / task_count) if task_count else 0.0


def _record_rank_key(item: tuple[int, VerificationRecord]) -> tuple[object, ...]:
    index, record = item
    if record.commit_block is not None:
        return (
            0,
            record.commit_block,
            _optional_position(record.commit_extrinsic_index),
            _optional_position(record.commit_event_index),
            _commitment_hash(record.commit_extrinsic_hash),
            record.received_at,
            index,
        )
    return (1, record.received_at, index)


def _commitment_hash(value: str | None) -> str:
    return hashlib.sha256((value or "").strip().encode()).hexdigest()


def _optional_position(value: int | None) -> int:
    return value if value is not None else 2**63 - 1
