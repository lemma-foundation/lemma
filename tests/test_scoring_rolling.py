"""Rolling verified-contribution scoring with decay."""

from __future__ import annotations

import pytest
from lemma.scoring.rolling import RollingConfig, decay_weight, rolling_scores, rolling_weights


def test_decay_weight_halves_at_half_life() -> None:
    assert decay_weight(0, half_life_epochs=3.0) == 1.0
    assert decay_weight(3, half_life_epochs=3.0) == pytest.approx(0.5)
    assert decay_weight(6, half_life_epochs=3.0) == pytest.approx(0.25)


def test_rolling_scores_decay_older_epochs() -> None:
    history = [{"a": 1.0}, {"a": 1.0}]
    scores = rolling_scores(history, config=RollingConfig(half_life_epochs=1.0))
    # Newest epoch (age 0) weight 1.0; previous (age 1) weight 0.5.
    assert scores["a"] == pytest.approx(1.5)


def test_rolling_scores_window_drops_old_credit() -> None:
    history = [{"a": 5.0}, {"b": 1.0}]
    scores = rolling_scores(history, config=RollingConfig(half_life_epochs=10.0, window=1))
    assert "a" not in scores
    assert scores["b"] == pytest.approx(1.0)


def test_quiet_recent_epoch_does_not_zero_prior_contribution() -> None:
    history = [{"a": 1.0}, {}]
    scores = rolling_scores(history, config=RollingConfig(half_life_epochs=3.0))
    assert scores["a"] > 0.0


def test_rolling_weights_normalize_to_one() -> None:
    history = [{"a": 1.0, "b": 1.0}]
    weights = rolling_weights(history)
    assert weights["a"] == pytest.approx(0.5)
    assert weights["b"] == pytest.approx(0.5)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_rolling_weights_empty_when_no_credit() -> None:
    assert rolling_weights([{}, {}]) == {}


def test_invalid_config_rejected() -> None:
    with pytest.raises(ValueError, match="half_life"):
        RollingConfig(half_life_epochs=0.0)
    with pytest.raises(ValueError, match="window"):
        RollingConfig(window=0)
