"""EMA solve-rate curriculum controller."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

CURRICULUM_RETARGET_VERSION = "lemma-curriculum-retarget-v1"


@dataclass(frozen=True)
class CurriculumConfig:
    beta: float = 0.8
    low_band: float = 0.40
    high_band: float = 0.70
    k_min: int = 20
    k_max: int = 5000
    cost_budget_s: float = 0.0
    base_task_cost_s: float = 0.0
    depth_cost_multiplier: float = 2.0
    window_base_blocks: int = 360
    window_max_blocks: int = 7200
    window_depth_multiplier: float = 2.0
    window_k_reference: int = 4


@dataclass(frozen=True)
class CurriculumState:
    active_K: int
    frontier_depth: int = 0
    ema_solve_rate: float = 0.50
    active_window_blocks: int = 360


@dataclass(frozen=True)
class CurriculumDecision:
    state: CurriculumState
    action: str
    variant_stream_requested: bool = False


@dataclass(frozen=True)
class CurriculumTempoRecord:
    tempo: int
    active_K: int
    frontier_depth: int
    ema_solve_rate: float
    solved_slots: int
    parked_task_ids: tuple[str, ...]
    action: str
    variant_stream_requested: bool
    active_window_blocks: int = 360
    activation_block: int | None = None
    retarget_receipt: dict[str, object] | None = None

    def to_json(self) -> str:
        payload: dict[str, object] = {
            "tempo": self.tempo,
            "active_K": self.active_K,
            "frontier_depth": self.frontier_depth,
            "active_window_blocks": self.active_window_blocks,
            "ema_solve_rate": self.ema_solve_rate,
            "solved_slots": self.solved_slots,
            "parked_task_ids": list(self.parked_task_ids),
            "action": self.action,
            "variant_stream_requested": self.variant_stream_requested,
        }
        if self.activation_block is not None:
            payload["activation_block"] = self.activation_block
        if self.retarget_receipt is not None:
            payload["retarget_receipt"] = self.retarget_receipt
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> CurriculumTempoRecord:
        data = json.loads(raw)
        return cls(
            tempo=int(data["tempo"]),
            active_K=int(data["active_K"]),
            frontier_depth=int(data["frontier_depth"]),
            active_window_blocks=int(data.get("active_window_blocks", 360)),
            ema_solve_rate=float(data["ema_solve_rate"]),
            solved_slots=int(data["solved_slots"]),
            parked_task_ids=tuple(str(item) for item in data.get("parked_task_ids", [])),
            action=str(data["action"]),
            variant_stream_requested=bool(data["variant_stream_requested"]),
            activation_block=int(data["activation_block"]) if data.get("activation_block") is not None else None,
            retarget_receipt=data.get("retarget_receipt"),
        )


def append_curriculum_record(path: Path, record: CurriculumTempoRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(record.to_json() + "\n")


def read_curriculum_records(path: Path) -> tuple[CurriculumTempoRecord, ...]:
    if not path.exists():
        return ()
    return tuple(
        CurriculumTempoRecord.from_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def retarget_curriculum(
    state: CurriculumState,
    *,
    solved_slots: int,
    validator_capacity: int,
    config: CurriculumConfig,
) -> CurriculumDecision:
    """Retarget depth from solve rate and K from validator/cost capacity."""
    if state.active_K <= 0:
        raise ValueError("active_K must be positive")
    if solved_slots < 0:
        raise ValueError("solved_slots must be non-negative")
    if not 0 <= config.beta < 1:
        raise ValueError("beta must be in [0, 1)")
    if config.k_max < config.k_min:
        raise ValueError("k_max must be >= k_min")
    if config.cost_budget_s < 0 or config.base_task_cost_s < 0:
        raise ValueError("cost budget and base task cost must be non-negative")
    if config.depth_cost_multiplier < 1:
        raise ValueError("depth cost multiplier must be at least 1")
    if config.window_base_blocks <= 0 or config.window_max_blocks < config.window_base_blocks:
        raise ValueError("window block bounds are invalid")
    if config.window_depth_multiplier < 1 or config.window_k_reference <= 0:
        raise ValueError("window scaling config is invalid")

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

    target_k = target_active_k(validator_capacity, frontier_depth=frontier, config=config)
    active_k = state.active_K
    frontier_advanced = frontier > state.frontier_depth
    if target_k > active_k and ema >= config.low_band and not frontier_advanced:
        active_k = min(target_k, active_k + max(1, active_k // 4))
    elif target_k < active_k:
        active_k = target_k

    return CurriculumDecision(
        state=CurriculumState(
            active_K=active_k,
            frontier_depth=frontier,
            ema_solve_rate=ema,
            active_window_blocks=target_window_blocks(frontier_depth=frontier, active_k=active_k, config=config),
        ),
        action=action,
        variant_stream_requested=variants,
    )


def estimated_task_cost_s(frontier_depth: int, config: CurriculumConfig) -> float | None:
    if config.cost_budget_s <= 0 or config.base_task_cost_s <= 0:
        return None
    return config.base_task_cost_s * (config.depth_cost_multiplier**frontier_depth)


def cost_limited_k(frontier_depth: int, config: CurriculumConfig) -> int | None:
    cost = estimated_task_cost_s(frontier_depth, config)
    if cost is None:
        return None
    return max(1, int(config.cost_budget_s // cost))


def target_active_k(validator_capacity: int, *, frontier_depth: int, config: CurriculumConfig) -> int:
    target = max(config.k_min, min(config.k_max, validator_capacity))
    cost_cap = cost_limited_k(frontier_depth, config)
    if cost_cap is not None:
        target = min(target, cost_cap)
    return max(1, target)


def target_window_blocks(*, frontier_depth: int, active_k: int, config: CurriculumConfig) -> int:
    """Return the deterministic block window for the next paid task set."""
    if frontier_depth < 0:
        raise ValueError("frontier_depth must be non-negative")
    if active_k <= 0:
        raise ValueError("active_k must be positive")
    if config.window_base_blocks <= 0 or config.window_max_blocks < config.window_base_blocks:
        raise ValueError("window block bounds are invalid")
    if config.window_depth_multiplier < 1 or config.window_k_reference <= 0:
        raise ValueError("window scaling config is invalid")

    depth_factor = config.window_depth_multiplier**frontier_depth
    k_factor = max(1, math.ceil(active_k / config.window_k_reference))
    raw_blocks = math.ceil(config.window_base_blocks * depth_factor * k_factor)
    rounded_blocks = math.ceil(raw_blocks / config.window_base_blocks) * config.window_base_blocks
    return min(config.window_max_blocks, max(config.window_base_blocks, rounded_blocks))


def curriculum_retarget_receipt(
    *,
    tempo: int,
    previous_state: CurriculumState,
    solved_slots: int,
    validator_capacity: int,
    config: CurriculumConfig,
    decision: CurriculumDecision,
    activation_block: int | None = None,
) -> dict[str, object]:
    if previous_state.active_K <= 0:
        raise ValueError("active_K must be positive")
    solve_rate = min(1.0, solved_slots / previous_state.active_K)
    cost_cap = cost_limited_k(decision.state.frontier_depth, config)
    estimated_cost = estimated_task_cost_s(decision.state.frontier_depth, config)
    receipt: dict[str, object] = {
        "version": CURRICULUM_RETARGET_VERSION,
        "activation_tempo": tempo + 2,
        "previous_active_K": previous_state.active_K,
        "previous_frontier_depth": previous_state.frontier_depth,
        "previous_ema_solve_rate": previous_state.ema_solve_rate,
        "solved_slots": solved_slots,
        "solve_rate": solve_rate,
        "validator_capacity": validator_capacity,
        "config": {
            "beta": config.beta,
            "low_band": config.low_band,
            "high_band": config.high_band,
            "k_min": config.k_min,
            "k_max": config.k_max,
            "cost_budget_s": config.cost_budget_s,
            "base_task_cost_s": config.base_task_cost_s,
            "depth_cost_multiplier": config.depth_cost_multiplier,
            "window_base_blocks": config.window_base_blocks,
            "window_max_blocks": config.window_max_blocks,
            "window_depth_multiplier": config.window_depth_multiplier,
            "window_k_reference": config.window_k_reference,
        },
        "next_active_K": decision.state.active_K,
        "next_frontier_depth": decision.state.frontier_depth,
        "next_active_window_blocks": decision.state.active_window_blocks,
        "next_ema_solve_rate": decision.state.ema_solve_rate,
    }
    if activation_block is not None:
        receipt["activation_block"] = activation_block
    if cost_cap is not None and estimated_cost is not None:
        receipt["next_cost_limited_K"] = cost_cap
        receipt["next_estimated_task_cost_s"] = estimated_cost
    return receipt
