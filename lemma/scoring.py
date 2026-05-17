"""First-accepted unique proof scoring for training tasks."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict


class VerificationRecord(BaseModel):
    """The small scoring view of a verified submission."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    task_version: int = 1
    target_sha256: str = ""
    solver_hotkey: str
    validator_hotkey: str = ""
    passed: bool
    proof_sha256: str
    proof_term_hash: str | None = None
    received_at: str = ""

    @property
    def proof_identity(self) -> str:
        return self.proof_term_hash or self.proof_sha256


@dataclass(frozen=True)
class ScoredProof:
    record: VerificationRecord
    proof_identity: str
    rewarded: bool


@dataclass(frozen=True)
class ScoreResult:
    winners: dict[str, str]
    credits: dict[str, int]
    weights: dict[str, float]
    valid_unique_proofs: tuple[ScoredProof, ...] = ()


def score_epoch(records: list[VerificationRecord]) -> ScoreResult:
    """Award one credit to the first accepted unique proof for each task."""
    by_task: dict[str, list[tuple[int, VerificationRecord]]] = defaultdict(list)
    for index, record in enumerate(records):
        if record.passed:
            by_task[record.task_id].append((index, record))

    winners: dict[str, str] = {}
    scored: list[ScoredProof] = []
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

    credits = dict(Counter(winners.values()))
    total = sum(credits.values())
    weights = {hotkey: credit / total for hotkey, credit in credits.items()} if total else {}
    return ScoreResult(winners=winners, credits=credits, weights=weights, valid_unique_proofs=tuple(scored))
