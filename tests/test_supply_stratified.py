"""Deterministic class-stratified active selection."""

from __future__ import annotations

from lemma.scoring.classes import task_reward_class
from lemma.supply.stratified import class_stratified_tasks
from lemma.task_supply import make_task
from lemma.tasks import LemmaTask

_SOURCE_VALUE = {0: "calibration", 1: "low", 2: "medium", 3: "high"}


def _task(index: int, reward_class: int) -> LemmaTask:
    task = make_task(
        task_id=f"task-{reward_class}-{index}",
        title=f"task {index}",
        theorem_name=f"t{reward_class}_{index}",
        type_expr="True",
        source_stream="lean_project",
        source_name="proj",
    )
    return task.model_copy(
        update={
            "task_class": "canary" if reward_class == 0 else "source_sorry",
            "source_value": _SOURCE_VALUE[reward_class],
        }
    )


def _backlog() -> list[LemmaTask]:
    tasks: list[LemmaTask] = []
    for reward_class in range(4):
        for index in range(4):
            tasks.append(_task(index, reward_class))
    return tasks


def test_reward_class_mapping() -> None:
    assert task_reward_class(_task(0, 0)) == 0
    assert task_reward_class(_task(0, 1)) == 1
    assert task_reward_class(_task(0, 2)) == 2
    assert task_reward_class(_task(0, 3)) == 3


def test_selection_is_deterministic() -> None:
    backlog = _backlog()
    first = class_stratified_tasks(backlog, active_K=6, seed="seed-a")
    second = class_stratified_tasks(backlog, active_K=6, seed="seed-a")
    assert [t.id for t in first] == [t.id for t in second]


def test_selection_spreads_across_classes() -> None:
    selected = class_stratified_tasks(_backlog(), active_K=8, seed="seed-a")
    classes = {task_reward_class(t) for t in selected}
    assert classes == {0, 1, 2, 3}


def test_quota_guarantees_canary_presence() -> None:
    # Heavily request paid classes but force at least one canary via quota.
    backlog = _backlog()
    selected = class_stratified_tasks(backlog, active_K=5, seed="seed-a", quotas={0: 1})
    canaries = [t for t in selected if task_reward_class(t) == 0]
    assert len(canaries) >= 1
    assert len(selected) == 5


def test_solved_tasks_are_excluded() -> None:
    backlog = _backlog()
    solved = {t.id for t in backlog if task_reward_class(t) == 1}
    selected = class_stratified_tasks(backlog, active_K=12, seed="seed-a", solved_task_ids=solved)
    assert all(t.id not in solved for t in selected)
    assert all(task_reward_class(t) != 1 for t in selected)


def test_active_k_larger_than_backlog_returns_all() -> None:
    backlog = _backlog()
    selected = class_stratified_tasks(backlog, active_K=999, seed="seed-a")
    assert {t.id for t in selected} == {t.id for t in backlog}


def test_zero_active_k_returns_empty() -> None:
    assert class_stratified_tasks(_backlog(), active_K=0, seed="seed-a") == ()
