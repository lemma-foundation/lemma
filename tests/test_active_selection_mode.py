"""Opt-in class-stratified active selection in the live validator path."""

from __future__ import annotations

from lemma.common.config import LemmaSettings
from lemma.scoring.classes import task_reward_class
from lemma.task_supply import make_task
from lemma.tasks import LemmaTask, TaskRegistry
from lemma.validator import active_tasks_for_validation

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


def _registry() -> TaskRegistry:
    tasks = tuple(_task(index, reward_class) for reward_class in range(4) for index in range(4))
    return TaskRegistry(schema_version=1, tasks=tasks, sha256="0" * 64)


def _settings(**overrides: object) -> LemmaSettings:
    return LemmaSettings(_env_file=None, active_task_count=6, **overrides)


def test_balanced_mode_is_default_and_returns_active_window() -> None:
    selected = active_tasks_for_validation(_registry(), _settings(), tempo=0)
    assert len(selected) == 6


def test_class_stratified_mode_spreads_classes() -> None:
    settings = _settings(active_selection_mode="class_stratified")
    selected = active_tasks_for_validation(_registry(), settings, tempo=0)
    assert len(selected) == 6
    assert len({task_reward_class(t) for t in selected}) >= 3


def test_class_stratified_mode_honors_canary_quota() -> None:
    settings = _settings(active_selection_mode="class_stratified", active_canary_quota=1)
    selected = active_tasks_for_validation(_registry(), settings, tempo=0)
    assert any(task_reward_class(t) == 0 for t in selected)


def test_class_stratified_mode_excludes_solved_backlog() -> None:
    registry = _registry()
    settings = _settings(active_selection_mode="class_stratified")
    solved = {t.id for t in registry.tasks if task_reward_class(t) == 2}
    selected = active_tasks_for_validation(registry, settings, tempo=0, solved_task_ids=solved)
    assert all(t.id not in solved for t in selected)


def test_class_stratified_mode_is_deterministic() -> None:
    registry = _registry()
    settings = _settings(active_selection_mode="class_stratified", active_canary_quota=1)
    first = active_tasks_for_validation(registry, settings, tempo=0)
    second = active_tasks_for_validation(registry, settings, tempo=0)
    assert [t.id for t in first] == [t.id for t in second]
    assert [t.queue_position for t in first] == list(range(len(first)))
