"""Small economic simulator for proof-unit scoring."""

from __future__ import annotations

import random
from dataclasses import dataclass

from lemma.scoring import VerificationRecord, score_epoch
from lemma.supply.controller import CurriculumConfig, CurriculumState, retarget_curriculum


@dataclass(frozen=True)
class MinerCapability:
    hotkey: str
    solve_power: float


@dataclass(frozen=True)
class TempoResult:
    tempo: int
    active_K: int
    frontier_depth: int
    solved_slots: int
    unearned_share: float
    miner_weights: dict[str, float]


def _choose_miner(rng: random.Random, miners: tuple[MinerCapability, ...], total_power: float) -> MinerCapability:
    mark = rng.random() * total_power
    acc = 0.0
    for miner in miners:
        acc += miner.solve_power
        if acc >= mark:
            return miner
    return miners[-1]


def simulate_tempos(
    miners: list[MinerCapability],
    *,
    tempos: int,
    initial_state: CurriculumState,
    validator_capacity: int,
    config: CurriculumConfig | None = None,
    seed: int = 0,
) -> list[TempoResult]:
    """Simulate solved-slot production without redistributing misses."""
    if not miners:
        raise ValueError("at least one miner is required")
    if any(miner.solve_power <= 0 for miner in miners):
        raise ValueError("miner solve_power values must be positive")
    rng = random.Random(seed)
    cfg = config or CurriculumConfig(k_min=1)
    state = initial_state
    miner_tuple = tuple(miners)
    total_power = sum(miner.solve_power for miner in miner_tuple)
    out: list[TempoResult] = []

    for tempo in range(tempos):
        records: list[VerificationRecord] = []
        hardness = max(1.0, state.frontier_depth + 1)
        solve_probability = min(0.98, total_power / (total_power + hardness))
        for slot in range(state.active_K):
            if rng.random() >= solve_probability:
                continue
            miner = _choose_miner(rng, miner_tuple, total_power)
            records.append(
                VerificationRecord(
                    task_id=f"tempo-{tempo}-slot-{slot}",
                    target_sha256=f"{slot:064x}"[-64:],
                    solver_hotkey=miner.hotkey,
                    passed=True,
                    proof_sha256=f"{tempo:032x}{slot:032x}"[-64:],
                    received_at=f"{tempo:08d}-{slot:08d}",
                )
            )
        score = score_epoch(records, active_task_count=state.active_K)
        out.append(
            TempoResult(
                tempo=tempo,
                active_K=state.active_K,
                frontier_depth=state.frontier_depth,
                solved_slots=len(score.winners),
                unearned_share=score.unearned_share,
                miner_weights=score.miner_weights,
            )
        )
        state = retarget_curriculum(
            state,
            solved_slots=len(score.winners),
            validator_capacity=validator_capacity,
            config=cfg,
        ).state
    return out
