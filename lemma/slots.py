"""Deterministic K-slot pool and depth retargeting."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ActiveSlot:
    slot_id: int
    task_id: str
    opened_tempo: int
    queue_depth: int


@dataclass(frozen=True)
class SlotPool:
    queue: tuple[str, ...]
    slots: tuple[ActiveSlot, ...]
    next_index: int


@dataclass(frozen=True)
class RetargetConfig:
    k_min: int = 20
    k_max: int = 5000
    ema_window: int = 8
    alpha_low: float = 0.40
    alpha_high: float = 0.70
    k_growth: float = 1.25
    k_shrink: float = 0.80


@dataclass(frozen=True)
class RetargetState:
    k: int
    queue_depth: int = 0
    solve_rate_ema: float = 0.50


def initial_pool(queue: list[str] | tuple[str, ...], *, k: int, tempo: int = 0, queue_depth: int = 0) -> SlotPool:
    if k < 0:
        raise ValueError("k must be non-negative")
    q = tuple(queue)
    if k > len(q):
        raise ValueError("k exceeds queue length")
    slots = tuple(ActiveSlot(slot_id=i, task_id=q[i], opened_tempo=tempo, queue_depth=queue_depth) for i in range(k))
    return SlotPool(queue=q, slots=slots, next_index=k)


def advance_pool(pool: SlotPool, *, solved_task_ids: set[str], tempo: int, t_max: int, queue_depth: int) -> SlotPool:
    """Replace solved or expired slots with the next deterministic queue entries."""
    if t_max < 1:
        raise ValueError("t_max must be positive")
    next_index = pool.next_index
    out: list[ActiveSlot] = []
    for slot in pool.slots:
        expired = tempo - slot.opened_tempo + 1 >= t_max
        if slot.task_id in solved_task_ids or expired:
            if next_index >= len(pool.queue):
                continue
            out.append(
                ActiveSlot(
                    slot_id=slot.slot_id,
                    task_id=pool.queue[next_index],
                    opened_tempo=tempo + 1,
                    queue_depth=queue_depth,
                )
            )
            next_index += 1
        else:
            out.append(slot)
    return SlotPool(queue=pool.queue, slots=tuple(out), next_index=next_index)


def retarget(
    state: RetargetState,
    *,
    solved_slots: int,
    validator_capacity: int,
    config: RetargetConfig,
) -> RetargetState:
    """Retarget queue depth by solve rate and K by validator throughput capacity."""
    if state.k <= 0:
        raise ValueError("state.k must be positive")
    if solved_slots < 0:
        raise ValueError("solved_slots must be non-negative")
    solve_rate = min(1.0, solved_slots / state.k)
    alpha = 2 / (config.ema_window + 1)
    ema = alpha * solve_rate + (1 - alpha) * state.solve_rate_ema

    depth = state.queue_depth
    if ema > config.alpha_high:
        depth += 1
    elif ema < config.alpha_low:
        depth = max(0, depth - 1)

    target_k = max(config.k_min, min(config.k_max, validator_capacity))
    if target_k > state.k:
        k = min(target_k, max(state.k + 1, int(state.k * config.k_growth)))
    elif target_k < state.k:
        k = max(target_k, int(state.k * config.k_shrink))
    else:
        k = state.k
    return RetargetState(k=k, queue_depth=depth, solve_rate_ema=ema)
