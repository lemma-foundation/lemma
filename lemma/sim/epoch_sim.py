"""Deterministic multi-epoch scoring simulation.

Each epoch:

1. select a class-stratified active set from the unsolved backlog;
2. let each miner attempt its targeted active tasks per its scripted behavior;
3. score the epoch with first-accepted unique-proof rules;
4. retire solved tasks so they leave the paid pool;
5. accumulate per-epoch credit for the rolling score.

Behaviors:

* ``honest`` - submits a unique valid proof (earliest honest miner wins a task);
* ``duplicate`` - resubmits the winning honest proof identity (earns nothing);
* ``invalid`` - submits a proof that fails to compile (``lean_compile_error``);
* ``timeout`` - submission times out (``timeout``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from lemma.scoring import VerificationRecord, score_epoch
from lemma.scoring.rolling import RollingConfig, rolling_scores
from lemma.supply.stratified import class_stratified_tasks
from lemma.tasks import LemmaTask

MinerBehavior = Literal["honest", "duplicate", "invalid", "timeout"]


@dataclass(frozen=True)
class MinerPlan:
    """One scripted miner for the simulation."""

    hotkey: str
    behavior: MinerBehavior
    order: int = 0
    targets: frozenset[str] | None = None

    def attempts(self, task_id: str) -> bool:
        return self.targets is None or task_id in self.targets


@dataclass(frozen=True)
class EpochOutcome:
    tempo: int
    active_task_ids: tuple[str, ...]
    winners: dict[str, str]
    credits: dict[str, int]


@dataclass
class SimResult:
    epochs: list[EpochOutcome] = field(default_factory=list)
    solved_task_ids: set[str] = field(default_factory=set)
    rolling_scores: dict[str, float] = field(default_factory=dict)


def _received_at(order: int) -> str:
    return f"2026-01-01T00:00:{order % 60:02d}Z"


def _honest_winner(active_id: str, miners: Sequence[MinerPlan]) -> MinerPlan | None:
    honest = [m for m in miners if m.behavior == "honest" and m.attempts(active_id)]
    if not honest:
        return None
    return min(honest, key=lambda m: (m.order, m.hotkey))


def _epoch_records(active: Sequence[LemmaTask], miners: Sequence[MinerPlan]) -> list[VerificationRecord]:
    records: list[VerificationRecord] = []
    for task in active:
        winner = _honest_winner(task.id, miners)
        winner_identity = f"{task.id}:{winner.hotkey}" if winner is not None else ""
        for miner in miners:
            if not miner.attempts(task.id):
                continue
            records.append(_record(task, miner, winner_identity))
    return records


def _record(task: LemmaTask, miner: MinerPlan, winner_identity: str) -> VerificationRecord:
    received_at = _received_at(miner.order)
    if miner.behavior == "honest":
        identity = f"{task.id}:{miner.hotkey}"
        return VerificationRecord(
            task_id=task.id,
            solver_hotkey=miner.hotkey,
            passed=True,
            proof_sha256=identity,
            proof_identity=identity,
            received_at=received_at,
        )
    if miner.behavior == "duplicate" and winner_identity:
        return VerificationRecord(
            task_id=task.id,
            solver_hotkey=miner.hotkey,
            passed=True,
            proof_sha256=winner_identity,
            proof_identity=winner_identity,
            received_at=received_at,
        )
    reason = "timeout" if miner.behavior == "timeout" else "lean_compile_error"
    if miner.behavior == "duplicate":
        reason = "duplicate_solution"
    return VerificationRecord(
        task_id=task.id,
        solver_hotkey=miner.hotkey,
        passed=False,
        reason=reason,
        proof_sha256=f"{task.id}:{miner.hotkey}:fail",
        proof_identity=f"{task.id}:{miner.hotkey}:fail",
        received_at=received_at,
    )


def simulate_epochs(
    tasks: Sequence[LemmaTask],
    *,
    miners: Sequence[MinerPlan],
    epochs: int,
    active_K: int,
    seed: str = "lemma-sim",
    quotas: dict[int, int] | None = None,
    rolling: RollingConfig | None = None,
) -> SimResult:
    """Run ``epochs`` epochs of selection + scoring + retirement deterministically."""
    if epochs < 0:
        raise ValueError("epochs must be non-negative")
    result = SimResult()
    history: list[dict[str, int]] = []

    for tempo in range(epochs):
        active = class_stratified_tasks(
            tasks,
            active_K=active_K,
            seed=f"{seed}:{tempo}",
            quotas=quotas,
            solved_task_ids=set(result.solved_task_ids),
        )
        records = _epoch_records(active, miners)
        scored = score_epoch(records, active_task_count=len(active))
        for task_id in scored.winners:
            result.solved_task_ids.add(task_id)
        credits = dict(scored.credits)
        history.append(credits)
        result.epochs.append(
            EpochOutcome(
                tempo=tempo,
                active_task_ids=tuple(task.id for task in active),
                winners=dict(scored.winners),
                credits=credits,
            )
        )

    result.rolling_scores = rolling_scores(history, config=rolling)
    return result
