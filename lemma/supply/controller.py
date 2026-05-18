"""EMA solve-rate curriculum controller."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CurriculumConfig:
    beta: float = 0.8
    low_band: float = 0.40
    high_band: float = 0.70
    k_min: int = 20
    k_max: int = 5000


@dataclass(frozen=True)
class CurriculumState:
    active_K: int
    frontier_depth: int = 0
    ema_solve_rate: float = 0.50


@dataclass(frozen=True)
class CurriculumDecision:
    state: CurriculumState
    action: str
    variant_stream_requested: bool = False


def retarget_curriculum(
    state: CurriculumState,
    *,
    solved_slots: int,
    validator_capacity: int,
    config: CurriculumConfig,
) -> CurriculumDecision:
    """Retarget depth from solve rate and K from validator capacity."""
    if state.active_K <= 0:
        raise ValueError("active_K must be positive")
    if solved_slots < 0:
        raise ValueError("solved_slots must be non-negative")
    if not 0 <= config.beta < 1:
        raise ValueError("beta must be in [0, 1)")

    solve_rate = min(1.0, solved_slots / state.active_K)
    ema = config.beta * state.ema_solve_rate + (1 - config.beta) * solve_rate
    frontier = state.frontier_depth
    action = "hold"
    variants = False

    if solved_slots == 0:
        action = "halt_frontier_and_request_variants"
        variants = True
    elif ema > config.high_band:
        frontier += 1
        action = "advance_frontier"
    elif ema < config.low_band:
        action = "hold_frontier_and_request_variants"
        variants = True

    target_k = max(config.k_min, min(config.k_max, validator_capacity))
    active_k = state.active_K
    if target_k > active_k and ema >= config.low_band:
        active_k = min(target_k, active_k + max(1, active_k // 4))
    elif target_k < active_k:
        active_k = max(target_k, active_k - max(1, active_k // 5))

    return CurriculumDecision(
        state=CurriculumState(active_K=active_k, frontier_depth=frontier, ema_solve_rate=ema),
        action=action,
        variant_stream_requested=variants,
    )
