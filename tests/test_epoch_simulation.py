"""Multi-epoch scoring simulation (Phase 4 deliverable)."""

from __future__ import annotations

from lemma.scoring.rolling import RollingConfig
from lemma.sim import MinerPlan, simulate_epochs
from lemma.task_supply import make_task
from lemma.tasks import LemmaTask

_SOLVABLE = [f"task-{i}" for i in range(4)]
_BROKEN = "task-broken"


def _task(task_id: str, reward_class: int = 1) -> LemmaTask:
    task = make_task(
        task_id=task_id,
        title=task_id,
        theorem_name=task_id.replace("-", "_"),
        type_expr="True",
        source_stream="lean_project",
        source_name="proj",
    )
    return task.model_copy(update={"task_class": "source_sorry", "source_value": "low"})


def _tasks() -> list[LemmaTask]:
    return [_task(tid) for tid in (*_SOLVABLE, _BROKEN)]


def _miners() -> list[MinerPlan]:
    solvable = frozenset(_SOLVABLE)
    return [
        MinerPlan(hotkey="h1", behavior="honest", order=0, targets=solvable),
        MinerPlan(hotkey="h2", behavior="honest", order=1, targets=solvable),
        MinerPlan(hotkey="dup", behavior="duplicate", order=2, targets=solvable),
        MinerPlan(hotkey="bad", behavior="invalid", order=3, targets=frozenset({_BROKEN})),
        MinerPlan(hotkey="slow", behavior="timeout", order=4, targets=frozenset({_BROKEN})),
    ]


def test_first_honest_miner_wins_each_task() -> None:
    result = simulate_epochs(_tasks(), miners=_miners(), epochs=3, active_K=5)
    all_winners = {hk for epoch in result.epochs for hk in epoch.winners.values()}
    assert all_winners == {"h1"}


def test_duplicate_invalid_and_timeout_earn_nothing() -> None:
    result = simulate_epochs(_tasks(), miners=_miners(), epochs=3, active_K=5)
    for epoch in result.epochs:
        assert "dup" not in epoch.credits
        assert "bad" not in epoch.credits
        assert "slow" not in epoch.credits
        assert "h2" not in epoch.credits  # valid alternate, but never first


def test_solved_tasks_retire_and_broken_task_persists() -> None:
    result = simulate_epochs(_tasks(), miners=_miners(), epochs=3, active_K=5)
    # All solvable tasks get solved (by h1) and never the broken one.
    assert set(_SOLVABLE) <= result.solved_task_ids
    assert _BROKEN not in result.solved_task_ids
    # After the first epoch solves them, solved tasks leave the active pool.
    assert set(result.epochs[1].active_task_ids).isdisjoint(_SOLVABLE)
    # The unsolved broken task keeps reappearing.
    assert _BROKEN in result.epochs[1].active_task_ids
    assert _BROKEN in result.epochs[2].active_task_ids


def test_rolling_scores_reward_only_winner() -> None:
    result = simulate_epochs(
        _tasks(),
        miners=_miners(),
        epochs=3,
        active_K=5,
        rolling=RollingConfig(half_life_epochs=2.0),
    )
    assert result.rolling_scores.get("h1", 0.0) > 0.0
    assert "dup" not in result.rolling_scores
    assert "h2" not in result.rolling_scores


def test_total_credit_matches_solvable_tasks() -> None:
    result = simulate_epochs(_tasks(), miners=_miners(), epochs=5, active_K=5)
    total_h1 = sum(epoch.credits.get("h1", 0) for epoch in result.epochs)
    assert total_h1 == len(_SOLVABLE)
