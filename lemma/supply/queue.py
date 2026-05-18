"""Deterministic K-slot active pool management."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from lemma.tasks import LemmaTask


@dataclass(frozen=True)
class ActiveSlot:
    slot_id: int
    task_id: str
    opened_tempo: int
    queue_position: int
    queue_depth: int


@dataclass(frozen=True)
class ParkedTask:
    task_id: str
    slot_id: int
    parked_tempo: int
    reason: str


@dataclass(frozen=True)
class ActivePool:
    queue: tuple[LemmaTask, ...]
    slots: tuple[ActiveSlot, ...]
    parked: tuple[ParkedTask, ...] = ()
    next_index: int = 0


def deterministic_task_queue(tasks: list[LemmaTask] | tuple[LemmaTask, ...], *, seed: str) -> tuple[LemmaTask, ...]:
    """Sort shallow tasks first, then by a seed-stable hash."""
    return tuple(
        sorted(
            tasks,
            key=lambda task: (
                task.queue_depth,
                hashlib.sha256(f"{seed}:{task.id}:{task.target_sha256}".encode()).hexdigest(),
            ),
        )
    )


def initial_active_pool(
    tasks: list[LemmaTask] | tuple[LemmaTask, ...],
    *,
    active_K: int,
    tempo: int,
    seed: str,
    frontier_depth: int,
) -> ActivePool:
    if active_K < 0:
        raise ValueError("active_K must be non-negative")
    queue = deterministic_task_queue(tuple(task for task in tasks if task.queue_depth <= frontier_depth), seed=seed)
    if active_K > len(queue):
        raise ValueError("active_K exceeds queue length")
    slots = tuple(
        ActiveSlot(
            slot_id=index,
            task_id=task.id,
            opened_tempo=tempo,
            queue_position=index,
            queue_depth=task.queue_depth,
        )
        for index, task in enumerate(queue[:active_K])
    )
    return ActivePool(queue=queue, slots=slots, next_index=active_K)


def advance_active_pool(
    pool: ActivePool,
    *,
    solved_task_ids: set[str],
    tempo: int,
    max_open_tempos: int,
) -> ActivePool:
    """Replace solved slots and park expired unsolved slots.

    Expiration never advances the frontier deeper by itself. If a hard target
    stalls, the controller is expected to add variant tasks to the queue before
    this function draws replacements.
    """
    if max_open_tempos < 1:
        raise ValueError("max_open_tempos must be positive")

    next_index = pool.next_index
    slots: list[ActiveSlot] = []
    parked: list[ParkedTask] = list(pool.parked)
    for slot in pool.slots:
        solved = slot.task_id in solved_task_ids
        expired = tempo - slot.opened_tempo + 1 >= max_open_tempos
        if not solved and not expired:
            slots.append(slot)
            continue
        if expired and not solved:
            parked.append(ParkedTask(task_id=slot.task_id, slot_id=slot.slot_id, parked_tempo=tempo, reason="expired"))
        if next_index >= len(pool.queue):
            continue
        task = pool.queue[next_index]
        slots.append(
            ActiveSlot(
                slot_id=slot.slot_id,
                task_id=task.id,
                opened_tempo=tempo + 1,
                queue_position=next_index,
                queue_depth=task.queue_depth,
            )
        )
        next_index += 1
    return ActivePool(queue=pool.queue, slots=tuple(slots), parked=tuple(parked), next_index=next_index)
