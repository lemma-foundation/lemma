"""Rolling verified-contribution scoring with epoch decay.

Per-epoch first-accepted credit (see :func:`lemma.scoring.score_epoch`) is the
authoritative payout for a single epoch. Miner *weights*, however, should not
collapse to zero just because a miner had a quiet epoch. The roadmap defines a
rolling score over recent epochs:

    rolling_score_miner = sum(task_credit_i * decay(age_i))

where ``age_i`` is how many epochs ago the credit was earned (newest = 0). This
module implements that as a pure, deterministic function over a credit history.
It introduces no new payout; it only smooths weights across epochs.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class RollingConfig:
    """Decay configuration for rolling contribution scores."""

    half_life_epochs: float = 3.0
    window: int | None = None

    def __post_init__(self) -> None:
        if self.half_life_epochs <= 0:
            raise ValueError("half_life_epochs must be positive")
        if self.window is not None and self.window <= 0:
            raise ValueError("window must be positive when set")


def decay_weight(age_epochs: int, *, half_life_epochs: float) -> float:
    """Return the exponential decay multiplier for credit ``age_epochs`` old."""
    if age_epochs < 0:
        raise ValueError("age_epochs must be non-negative")
    return 0.5 ** (age_epochs / half_life_epochs)


def rolling_scores(
    epoch_credits: Sequence[Mapping[str, float]],
    *,
    config: RollingConfig | None = None,
) -> dict[str, float]:
    """Return decayed rolling scores from an ordered credit history.

    ``epoch_credits`` is ordered oldest-first; the last entry is the most recent
    epoch (age 0). Each entry maps ``hotkey -> credit`` for that epoch. Credits
    older than ``config.window`` epochs are dropped.
    """
    cfg = config or RollingConfig()
    count = len(epoch_credits)
    scores: dict[str, float] = defaultdict(float)
    for index, credits in enumerate(epoch_credits):
        age = (count - 1) - index
        if cfg.window is not None and age >= cfg.window:
            continue
        weight = decay_weight(age, half_life_epochs=cfg.half_life_epochs)
        for hotkey, credit in credits.items():
            scores[hotkey] += float(credit) * weight
    return dict(scores)


def rolling_weights(
    epoch_credits: Sequence[Mapping[str, float]],
    *,
    config: RollingConfig | None = None,
) -> dict[str, float]:
    """Return rolling scores normalized to sum to 1 (empty when no credit)."""
    scores = rolling_scores(epoch_credits, config=config)
    total = sum(scores.values())
    if total <= 0:
        return {}
    return {hotkey: value / total for hotkey, value in scores.items()}
