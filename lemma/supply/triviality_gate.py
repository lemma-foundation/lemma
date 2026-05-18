"""Triviality-gate labels for generated tasks."""

from __future__ import annotations

from typing import Literal

TrivialityLabel = Literal["trivial_curriculum", "paid_easy", "paid_medium", "paid_frontier"]

TRIVIALITY_TACTICS: tuple[str, ...] = ("decide", "simp_all", "aesop", "omega", "norm_num", "ring")


def label_from_baseline(*, solved_by_baseline: bool, queue_depth: int) -> TrivialityLabel:
    if solved_by_baseline:
        return "trivial_curriculum"
    if queue_depth <= 1:
        return "paid_easy"
    if queue_depth <= 3:
        return "paid_medium"
    return "paid_frontier"
