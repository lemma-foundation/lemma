"""Local, deterministic multi-epoch simulation of the real-task scoring loop.

This package wires the real Phase 4 primitives - class-stratified active
selection, first-accepted unique-proof scoring, solved-task retirement, and
rolling decay - into an offline simulation. It exists to prove the loop behaves
correctly against honest, duplicate, invalid, and timeout miners without a chain
or a Lean toolchain. It is test/operator tooling, never a runtime mining path.
"""

from __future__ import annotations

from lemma.sim.epoch_sim import (
    EpochOutcome,
    MinerPlan,
    SimResult,
    simulate_epochs,
)

__all__ = [
    "EpochOutcome",
    "MinerPlan",
    "SimResult",
    "simulate_epochs",
]
