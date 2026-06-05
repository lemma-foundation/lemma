"""Deterministic class-stratified active-task selection.

Two validators with the same public backlog, seed, and solved-set must select
the same active tasks. This selector:

* drops already-solved tasks (retirement from the paid pool);
* groups the remaining backlog by roadmap reward class;
* orders each class deterministically (level/family balanced);
* fills the active set by per-class minimum quotas, then round-robins across
  classes in class order until ``active_K`` is reached.

It introduces no randomness beyond the supplied ``seed``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Container, Mapping, Sequence

from lemma.scoring.classes import task_reward_class
from lemma.task_supply import deterministic_queue
from lemma.tasks import LemmaTask


def class_stratified_tasks(
    tasks: Sequence[LemmaTask],
    *,
    active_K: int,
    seed: str,
    quotas: Mapping[int, int] | None = None,
    solved_task_ids: Container[str] = frozenset(),
) -> tuple[LemmaTask, ...]:
    """Select up to ``active_K`` unsolved tasks, stratified by reward class."""
    if active_K < 0:
        raise ValueError("active_K must be non-negative")
    if active_K == 0:
        return ()

    backlog = [task for task in tasks if task.id not in solved_task_ids]
    by_class: dict[int, list[LemmaTask]] = defaultdict(list)
    for task in backlog:
        by_class[task_reward_class(task)].append(task)

    ordered: dict[int, list[LemmaTask]] = {
        reward_class: list(deterministic_queue(members, seed=f"{seed}:class{reward_class}"))
        for reward_class, members in by_class.items()
    }
    class_order = sorted(ordered)

    selected: list[LemmaTask] = []
    taken: set[str] = set()

    def _take(reward_class: int) -> bool:
        for task in ordered.get(reward_class, ()):
            if task.id not in taken:
                taken.add(task.id)
                selected.append(task)
                return True
        return False

    if quotas:
        for reward_class in class_order:
            want = quotas.get(reward_class, 0)
            for _ in range(want):
                if len(selected) >= active_K or not _take(reward_class):
                    break

    while len(selected) < active_K:
        progressed = False
        for reward_class in class_order:
            if len(selected) >= active_K:
                break
            if _take(reward_class):
                progressed = True
        if not progressed:
            break

    return tuple(selected[:active_K])
