"""Open AlphaProof queue, simulator, and chain-interface contracts."""

from __future__ import annotations

from lemma.chain.burn_or_recycle import UnearnedAllocation
from lemma.chain.commitments import CommitmentEnvelope, ciphertext_sha256
from lemma.chain.weights import allocation_vector
from lemma.simulate import MinerCapability, simulate_tempos
from lemma.supply import auto_formalize, conjecture_gen, mathlib_snapshot, perturbations, state_graph, variants
from lemma.supply.controller import CurriculumConfig, CurriculumState, retarget_curriculum
from lemma.supply.queue import advance_active_pool, initial_active_pool


def _fixture_tasks():
    candidates = (
        mathlib_snapshot.fixture_candidates()
        + perturbations.fixture_candidates()
        + state_graph.fixture_candidates()
        + auto_formalize.fixture_candidates()
        + conjecture_gen.fixture_candidates()
        + variants.fixture_candidates("lemma.fixture.hard")
    )
    return [candidate.to_task(queue_position=index) for index, candidate in enumerate(candidates)]


def test_active_pool_is_deterministic_and_parks_expired_tasks() -> None:
    tasks = _fixture_tasks()
    pool = initial_active_pool(tasks, active_K=2, tempo=0, seed="tempo-0", frontier_depth=2)

    next_pool = advance_active_pool(pool, solved_task_ids={pool.slots[0].task_id}, tempo=1, max_open_tempos=3)

    assert len(next_pool.slots) == 2
    assert next_pool.slots[0].opened_tempo == 2
    assert next_pool.parked == ()

    parked_pool = advance_active_pool(pool, solved_task_ids=set(), tempo=1, max_open_tempos=2)

    assert len(parked_pool.parked) == 2
    assert {item.reason for item in parked_pool.parked} == {"expired"}


def test_curriculum_halts_frontier_and_requests_variants_on_zero_solve() -> None:
    state = CurriculumState(active_K=20, frontier_depth=4, ema_solve_rate=0.4)
    decision = retarget_curriculum(
        state,
        solved_slots=0,
        validator_capacity=50,
        config=CurriculumConfig(k_min=10, k_max=100),
    )

    assert decision.state.frontier_depth == 4
    assert decision.action == "halt_frontier_and_request_variants"
    assert decision.variant_stream_requested is True


def test_curriculum_separates_depth_from_validator_capacity() -> None:
    config = CurriculumConfig(beta=0.0, low_band=0.4, high_band=0.7, k_min=10, k_max=100)
    state = CurriculumState(active_K=20, frontier_depth=2, ema_solve_rate=0.5)

    harder = retarget_curriculum(state, solved_slots=18, validator_capacity=20, config=config)
    more_capacity = retarget_curriculum(state, solved_slots=10, validator_capacity=40, config=config)

    assert harder.state.frontier_depth == 3
    assert harder.state.active_K == 20
    assert more_capacity.state.frontier_depth == 2
    assert more_capacity.state.active_K > 20


def test_economic_simulator_never_redistributes_unsolved_slots() -> None:
    rows = simulate_tempos(
        [MinerCapability("hk-a", 1.0), MinerCapability("hk-b", 0.35)],
        tempos=100,
        initial_state=CurriculumState(active_K=10, frontier_depth=3),
        validator_capacity=20,
        config=CurriculumConfig(k_min=5, k_max=30),
        seed=11,
    )

    assert len(rows) == 100
    assert all(sum(row.miner_weights.values()) <= 1.0 for row in rows)
    assert any(row.unearned_share > 0 for row in rows)


def test_chain_interfaces_are_deterministic_without_live_chain_claims() -> None:
    envelope = CommitmentEnvelope(
        task_id="task",
        target_sha256="0" * 64,
        miner_hotkey="hk",
        drand_round=10,
        ciphertext_sha256=ciphertext_sha256(b"proof"),
        commit_block=5,
        extrinsic_hash="0xabc",
    )
    later = envelope.model_copy(update={"commit_block": 6, "extrinsic_hash": "0xdef"})

    assert envelope.rank_key("seed") < later.rank_key("seed")
    assert envelope.signing_payload() == envelope.model_copy().signing_payload()
    assert allocation_vector({"hk": 0.2}, UnearnedAllocation(policy="burn", share=0.8, uid=0)) == {
        "hk": 0.2,
        "burn_uid:0": 0.8,
    }
