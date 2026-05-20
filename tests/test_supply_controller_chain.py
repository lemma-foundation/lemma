"""Open AlphaProof queue, simulator, and chain-interface contracts."""

from __future__ import annotations

from lemma.chain.burn_or_recycle import UnearnedAllocation
from lemma.chain.commitments import CommitmentEnvelope, ciphertext_sha256
from lemma.chain.weights import _wait_for_commit_reveal_window, allocation_vector, resolve_weight_plan
from lemma.simulate import MinerCapability, simulate_tempos
from lemma.supply import auto_formalize, conjecture_gen, mathlib_snapshot, perturbations, state_graph, variants
from lemma.supply.controller import (
    CurriculumConfig,
    CurriculumState,
    CurriculumTempoRecord,
    append_curriculum_record,
    read_curriculum_records,
    retarget_curriculum,
)
from lemma.supply.mixed import build_mixed_registry_tasks
from lemma.supply.procedural import build_procedural_registry_tasks
from lemma.supply.queue import advance_active_pool, initial_active_pool
from lemma.tasks import SourceRef


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


def test_curriculum_records_persist_per_tempo(tmp_path) -> None:
    path = tmp_path / "curriculum.jsonl"
    record = CurriculumTempoRecord(
        tempo=7,
        active_K=20,
        frontier_depth=3,
        ema_solve_rate=0.45,
        solved_slots=4,
        parked_task_ids=("task-a",),
        action="hold",
        variant_stream_requested=False,
    )

    append_curriculum_record(path, record)

    assert read_curriculum_records(path) == (record,)


def test_mixed_registry_requires_launch_vetted_candidates() -> None:
    good = mathlib_snapshot.fixture_candidates()[0].model_copy(
        update={
            "metadata": {
                "typechecked": True,
                "triviality_checked": True,
                "baseline_solved": False,
                "near_duplicate_score": 0.1,
            }
        }
    )
    rejected = conjecture_gen.fixture_candidates()[0]

    build = build_mixed_registry_tasks((good, rejected), seed="mixed", frontier_depth=2)

    assert [task.id for task in build.tasks] == [good.id]
    assert build.tasks[0].activation_status == "paid"
    assert build.tasks[0].triviality_status == "paid_easy"
    assert build.rejected[0].reason == "typecheck_not_confirmed"


def test_procedural_registry_requires_depth_two_metadata() -> None:
    metadata = {
        "activation_status": "paid",
        "supply_mode": "procedural",
        "mutation_depth": 2,
        "mutation_chain": [
            {"operator": "generalize", "input_hash": "1" * 64, "output_hash": "2" * 64},
            {"operator": "specialize", "input_hash": "2" * 64, "output_hash": "3" * 64},
        ],
        "generation_seed": "tempo-0",
        "drand_round": 10,
        "anchor_block": 360,
        "source_pool_hash": "4" * 64,
        "operator_bundle_hash": "5" * 64,
        "canonical_hash": "6" * 64,
        "typechecked": True,
        "prop_gate_passed": True,
        "triviality_checked": True,
        "baseline_solved": False,
        "novelty_status": "passed",
        "slot_weight": 2.0,
        "license_state": "clean_open",
    }
    good = mathlib_snapshot.fixture_candidates()[0].model_copy(
        update={
            "id": "lemma.procedural.depth2",
            "source_stream": "procedural",
            "source_ref": SourceRef(kind="procedural", name="tempo-0-depth2"),
            "metadata": metadata,
        }
    )
    depth_one = good.model_copy(
        update={
            "id": "lemma.procedural.depth1",
            "metadata": {**metadata, "mutation_depth": 1, "mutation_chain": metadata["mutation_chain"][:1]},
        }
    )

    build = build_procedural_registry_tasks((good, depth_one), seed="procedural", frontier_depth=2)

    assert [task.id for task in build.tasks] == [good.id]
    assert build.tasks[0].source_stream == "procedural"
    assert build.tasks[0].metadata["slot_weight"] == 2.0
    assert build.rejected[0].reason == "mutation_depth"


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
    assert all(row.solve_rate == row.solved_slots / row.active_K for row in rows)
    assert all(0.0 <= row.ema_solve_rate <= 1.0 for row in rows)
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


def test_chain_weight_plan_resolves_hotkeys_and_uid_labels() -> None:
    plan = resolve_weight_plan({"hk-b": 0.25, "burn_uid:0": 0.75}, ["burn", "hk-a", "hk-b"])

    assert plan.uids == (0, 2)
    assert plan.weights == (0.75, 0.25)


def test_chain_weight_plan_rejects_unknown_hotkey() -> None:
    try:
        resolve_weight_plan({"unknown-hotkey": 1.0}, ["hk-a"])
    except ValueError as e:
        assert "cannot resolve" in str(e)
    else:
        raise AssertionError("unknown hotkey should fail closed")


def test_commit_reveal_window_waits_until_last_ten_blocks(monkeypatch) -> None:
    class Hyperparams:
        tempo = 360

    class Subtensor:
        blocks = [100, 350]

        def commit_reveal_enabled(self, *, netuid: int) -> bool:
            assert netuid == 467
            return True

        def get_current_block(self) -> int:
            return self.blocks.pop(0)

        def get_subnet_hyperparameters(self, netuid: int, *, block: int) -> Hyperparams:
            assert netuid == 467
            assert block in {100, 350}
            return Hyperparams()

    sleeps: list[float] = []
    logs: list[tuple[object, ...]] = []
    monkeypatch.setattr("lemma.chain.weights.time.sleep", sleeps.append)
    monkeypatch.setattr("lemma.chain.weights.logger.info", lambda *args: logs.append(args))

    _wait_for_commit_reveal_window(Subtensor(), 467)

    assert sleeps == [12.0]
    assert logs
