"""Deterministic triviality-budget retargeting from public burn history."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TRIVIALITY_BUDGET_VERSION = "lemma-triviality-retarget-v1"


@dataclass(frozen=True)
class BurnRateRecord:
    tempo: int
    burn_rate_basis_points: int


@dataclass(frozen=True)
class TrivialityRetargetConfig:
    genesis_budget_s: int = 120
    min_budget_s: int = 1
    max_budget_s: int = 1200
    window_tempos: int = 8
    low_burn_basis_points: int = 4000
    high_burn_basis_points: int = 7000
    max_step_basis_points: int = 2500

    def __post_init__(self) -> None:
        if self.genesis_budget_s < 1:
            raise ValueError("genesis_budget_s must be positive")
        if self.min_budget_s < 1:
            raise ValueError("min_budget_s must be positive")
        if self.max_budget_s < self.min_budget_s:
            raise ValueError("max_budget_s must be >= min_budget_s")
        if self.window_tempos < 1:
            raise ValueError("window_tempos must be positive")
        if not 0 <= self.low_burn_basis_points < self.high_burn_basis_points <= 10000:
            raise ValueError("burn bands must satisfy 0 <= low < high <= 10000")
        if not 1 <= self.max_step_basis_points <= 10000:
            raise ValueError("max_step_basis_points must be in [1, 10000]")


@dataclass(frozen=True)
class TrivialityBudgetReceipt:
    budget_s: int
    burn_rate_basis_points: int | None
    inputs: dict[str, object]

    def metadata(self) -> dict[str, object]:
        return {
            "triviality_budget_version": TRIVIALITY_BUDGET_VERSION,
            "triviality_budget_s": self.budget_s,
            "triviality_burn_rate_basis_points": self.burn_rate_basis_points,
            "triviality_retarget_inputs": self.inputs,
        }


def triviality_budget_receipt(
    records: tuple[BurnRateRecord, ...],
    *,
    tempo: int,
    config: TrivialityRetargetConfig,
) -> TrivialityBudgetReceipt:
    if tempo < 0:
        raise ValueError("tempo must be non-negative")
    prior = tuple(sorted((record for record in records if record.tempo < tempo), key=lambda item: item.tempo))
    budget = _clamp_budget(config.genesis_budget_s, config)
    window: list[int] = []
    rolling_burn: int | None = None
    for record in prior:
        if record.tempo < 0:
            raise ValueError("record tempo must be non-negative")
        if not 0 <= record.burn_rate_basis_points <= 10000:
            raise ValueError("burn_rate_basis_points must be in [0, 10000]")
        window.append(record.burn_rate_basis_points)
        rolling_burn = _rolling_average(window[-config.window_tempos :])
        budget = _retarget_budget(budget, rolling_burn, config)

    return TrivialityBudgetReceipt(
        budget_s=budget,
        burn_rate_basis_points=rolling_burn,
        inputs={
            "version": TRIVIALITY_BUDGET_VERSION,
            "target_tempo": tempo,
            "genesis_budget_s": config.genesis_budget_s,
            "min_budget_s": config.min_budget_s,
            "max_budget_s": config.max_budget_s,
            "window_tempos": config.window_tempos,
            "low_burn_basis_points": config.low_burn_basis_points,
            "high_burn_basis_points": config.high_burn_basis_points,
            "target_burn_basis_points": _target_burn(config),
            "max_step_basis_points": config.max_step_basis_points,
            "settlement_count": len(prior),
            "latest_settlement_tempo": prior[-1].tempo if prior else None,
            "settlement_history_sha256": burn_history_sha256(prior),
        },
    )


def static_triviality_budget_receipt(budget_s: int) -> TrivialityBudgetReceipt:
    config = TrivialityRetargetConfig(genesis_budget_s=budget_s, max_budget_s=max(1200, budget_s))
    return triviality_budget_receipt((), tempo=0, config=config)


def triviality_budget_receipt_for_settings(settings: Any, *, tempo: int) -> TrivialityBudgetReceipt:
    max_budget = min(int(settings.procedural_triviality_max_budget_s), int(settings.lean_verify_timeout_s))
    config = TrivialityRetargetConfig(
        genesis_budget_s=int(settings.procedural_triviality_budget_s),
        min_budget_s=int(settings.procedural_triviality_min_budget_s),
        max_budget_s=max(max_budget, int(settings.procedural_triviality_min_budget_s)),
        window_tempos=int(settings.procedural_triviality_retarget_window_tempos),
        low_burn_basis_points=_rate_to_basis_points(settings.procedural_triviality_low_burn_rate),
        high_burn_basis_points=_rate_to_basis_points(settings.procedural_triviality_high_burn_rate),
        max_step_basis_points=_rate_to_basis_points(settings.procedural_triviality_max_step_rate),
    )
    path = settings.procedural_triviality_retarget_jsonl
    records = read_burn_rate_records(path) if path is not None else ()
    return triviality_budget_receipt(records, tempo=tempo, config=config)


def read_burn_rate_records(path: Path) -> tuple[BurnRateRecord, ...]:
    if not path.exists():
        raise FileNotFoundError(f"triviality retarget history not found: {path}")
    by_tempo: dict[int, BurnRateRecord] = {}
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}:{no}: invalid JSON") from e
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{no}: expected object")
        try:
            tempo = int(row["tempo"])
            burn_rate = _rate_to_basis_points(row["unearned_share"])
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"{path}:{no}: expected tempo and unearned_share") from e
        by_tempo[tempo] = BurnRateRecord(tempo=tempo, burn_rate_basis_points=burn_rate)
    return tuple(by_tempo[tempo] for tempo in sorted(by_tempo))


def burn_history_sha256(records: tuple[BurnRateRecord, ...]) -> str:
    payload = [
        {"tempo": record.tempo, "burn_rate_basis_points": record.burn_rate_basis_points}
        for record in records
    ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _retarget_budget(current_budget_s: int, burn_basis_points: int, config: TrivialityRetargetConfig) -> int:
    if config.low_burn_basis_points <= burn_basis_points <= config.high_burn_basis_points:
        return current_budget_s
    target = _target_burn(config)
    if burn_basis_points < config.low_burn_basis_points:
        factor = min(10000 + config.max_step_basis_points, (10000 * target) // max(1, burn_basis_points))
    else:
        factor = max(10000 - config.max_step_basis_points, (10000 * target) // burn_basis_points)
    return _clamp_budget((current_budget_s * factor + 5000) // 10000, config)


def _rolling_average(values: list[int]) -> int:
    return (sum(values) + len(values) // 2) // len(values)


def _target_burn(config: TrivialityRetargetConfig) -> int:
    return (config.low_burn_basis_points + config.high_burn_basis_points) // 2


def _clamp_budget(value: int, config: TrivialityRetargetConfig) -> int:
    return max(config.min_budget_s, min(config.max_budget_s, int(value)))


def _rate_to_basis_points(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("rate must be numeric")
    if not isinstance(value, (int, float, str)):
        raise ValueError("rate must be numeric")
    rate = float(value)
    if not 0.0 <= rate <= 1.0:
        raise ValueError("rate must be in [0, 1]")
    return int(round(rate * 10000))
