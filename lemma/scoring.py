"""First-accepted unique proof scoring for training tasks."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict


class VerificationRecord(BaseModel):
    """The small scoring view of a verified submission."""

    model_config = ConfigDict(extra="ignore")

    task_id: str
    solver_hotkey: str
    passed: bool
    proof_sha256: str
    proof_term_hash: str | None = None
    received_at: str = ""

    @property
    def proof_identity(self) -> str:
        return self.proof_term_hash or self.proof_sha256


@dataclass(frozen=True)
class ScoreResult:
    winners: dict[str, str]
    credits: dict[str, int]
    weights: dict[str, float]


def score_epoch(records: list[VerificationRecord]) -> ScoreResult:
    """Award one credit to the first accepted unique proof for each task."""
    by_task: dict[str, list[tuple[int, VerificationRecord]]] = defaultdict(list)
    for index, record in enumerate(records):
        if record.passed:
            by_task[record.task_id].append((index, record))

    winners: dict[str, str] = {}
    for task_id, task_records in by_task.items():
        ordered = sorted(task_records, key=lambda item: (item[1].received_at, item[0]))
        seen: set[str] = set()
        for _, record in ordered:
            if record.proof_identity in seen:
                continue
            seen.add(record.proof_identity)
            winners[task_id] = record.solver_hotkey
            break

    credits = dict(Counter(winners.values()))
    total = sum(credits.values())
    weights = {hotkey: credit / total for hotkey, credit in credits.items()} if total else {}
    return ScoreResult(winners=winners, credits=credits, weights=weights)
