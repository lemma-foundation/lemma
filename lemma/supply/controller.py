"""EMA solve-rate curriculum controller."""

from __future__ import annotations

import json
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
    retarget_receipt: dict[str, object] | None = None

    def to_json(self) -> str:
        payload: dict[str, object] = {
            "tempo": self.tempo,
            "active_K": self.active_K,
            "frontier_depth": self.frontier_depth,
            "ema_solve_rate": self.ema_solve_rate,
            "solved_slots": self.solved_slots,
            "parked_task_ids": list(self.parked_task_ids),
            "action": self.action,
            "variant_stream_requested": self.variant_stream_requested,
        }
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
            ema_solve_rate=float(data["ema_solve_rate"]),
            solved_slots=int(data["solved_slots"]),
            parked_task_ids=tuple(str(item) for item in data.get("parked_task_ids", [])),
            action=str(data["action"]),
            variant_stream_requested=bool(data["variant_stream_requested"]),
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


def curriculum_retarget_receipt(
    *,
    tempo: int,
    previous_state: CurriculumState,
    solved_slots: int,
    validator_capacity: int,
    config: CurriculumConfig,
    decision: CurriculumDecision,
) -> dict[str, object]:
    if previous_state.active_K <= 0:
        raise ValueError("active_K must be positive")
    solve_rate = min(1.0, solved_slots / previous_state.active_K)
    return {
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
        },
        "next_active_K": decision.state.active_K,
        "next_frontier_depth": decision.state.frontier_depth,
        "next_ema_solve_rate": decision.state.ema_solve_rate,
    }
