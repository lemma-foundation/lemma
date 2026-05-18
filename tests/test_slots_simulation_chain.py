"""V3 slot, simulator, and chain-envelope contracts."""

from __future__ import annotations

from lemma.chain.commitments import CommitmentEnvelope, ciphertext_sha256
from lemma.simulate import MinerCapability, simulate_tempos
from lemma.slots import RetargetConfig, RetargetState, advance_pool, initial_pool, retarget
from lemma.supply.controller import CurriculumConfig, CurriculumState


def test_slot_pool_replaces_solved_and_expired_slots_deterministically() -> None:
    pool = initial_pool(["a", "b", "c", "d", "e"], k=2, tempo=0, queue_depth=0)

    next_pool = advance_pool(pool, solved_task_ids={"a"}, tempo=1, t_max=2, queue_depth=3)

    assert [slot.task_id for slot in next_pool.slots] == ["c", "d"]
    assert [slot.queue_depth for slot in next_pool.slots] == [3, 3]
    assert next_pool.next_index == 4


def test_retarget_separates_depth_from_validator_capacity() -> None:
    config = RetargetConfig(k_min=10, k_max=100, ema_window=1, alpha_low=0.4, alpha_high=0.7)
    state = RetargetState(k=20, queue_depth=2, solve_rate_ema=0.5)

    harder = retarget(state, solved_slots=18, validator_capacity=20, config=config)
    more_capacity = retarget(state, solved_slots=10, validator_capacity=40, config=config)

    assert harder.queue_depth == 3
    assert harder.k == 20
    assert more_capacity.queue_depth == 2
    assert more_capacity.k > 20


def test_economic_simulator_never_redistributes_unsolved_slots() -> None:
    rows = simulate_tempos(
        [MinerCapability("a", 1.0), MinerCapability("b", 0.5)],
        tempos=25,
        initial_state=CurriculumState(active_K=10, frontier_depth=3),
        validator_capacity=10,
        config=CurriculumConfig(k_min=5, k_max=20),
        seed=7,
    )

    assert len(rows) == 25
    assert all(sum(row.miner_weights.values()) <= 1.0 for row in rows)
    assert any(row.unearned_share > 0 for row in rows)


def test_commitment_envelope_orders_by_chain_block_then_seeded_tie_break() -> None:
    first = CommitmentEnvelope(
        task_id="t",
        target_sha256="0" * 64,
        miner_hotkey="hk",
        drand_round=10,
        ciphertext_sha256=ciphertext_sha256(b"proof"),
        commit_block=5,
        extrinsic_hash="a",
    )
    later = first.model_copy(update={"commit_block": 6, "extrinsic_hash": "b"})

    assert first.rank_key("seed") < later.rank_key("seed")
    assert first.signing_payload() == first.model_copy().signing_payload()
