"""Open AlphaProof queue, simulator, and chain-interface contracts."""

from __future__ import annotations

import json

from lemma.chain.burn_or_recycle import UnearnedAllocation
from lemma.chain.commitments import CommitmentEnvelope, ciphertext_sha256
from lemma.chain.weights import _wait_for_commit_reveal_window, allocation_vector, resolve_weight_plan
from lemma.protocol_invariants import procedural_gate_receipt_sha256
from lemma.simulate import MinerCapability, simulate_tempos
from lemma.supply import auto_formalize, conjecture_gen, mathlib_snapshot, perturbations, state_graph, variants
from lemma.supply.controller import (
    CurriculumConfig,
    CurriculumState,
    CurriculumTempoRecord,
    append_curriculum_record,
    curriculum_retarget_receipt,
    read_curriculum_records,
    retarget_curriculum,
)
from lemma.supply.gates import GATE_VERSION
from lemma.supply.import_graph import ImportGraphRow, import_graph_from_rows
from lemma.supply.mixed import build_mixed_registry_tasks
from lemma.supply.novelty import novelty_cache_from_hashes
from lemma.supply.operator_bundle import MUTATION_ENGINE, OPERATOR_BUNDLE_VERSION, procedural_operator_bundle_hash
from lemma.supply.procedural import build_procedural_registry_tasks
from lemma.supply.queue import advance_active_pool, initial_active_pool
from lemma.supply.slot_weight import slot_weight_receipt_for_candidate
from lemma.supply.source_pool import source_pool_receipt, source_pool_receipt_sha256
from lemma.supply.triviality_budget import BurnRateRecord, TrivialityRetargetConfig, triviality_budget_receipt
from lemma.task_supply import make_task
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


def _import_graph():
    return import_graph_from_rows(
        (
            ImportGraphRow(module="Mathlib.Source", imports=("Mathlib.Init",)),
            ImportGraphRow(module="Mathlib", imports=("Mathlib.Init",)),
            ImportGraphRow(module="Mathlib.Init", imports=()),
        )
    )


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


def test_active_pool_interleaves_frontier_and_foundation_levels() -> None:
    tasks = tuple(
        make_task(
            task_id=f"lemma.test.depth-{depth}",
            title=f"Depth {depth}",
            theorem_name=f"depth_{depth}",
            type_expr="True",
            source_stream="mathlib_snapshot",
            source_name=f"depth-{depth}",
            queue_depth=depth,
        )
        for depth in range(9)
    )

    pool = initial_active_pool(tasks, active_K=6, tempo=0, seed="tempo-0", frontier_depth=8)

    assert [slot.queue_depth for slot in pool.slots] == [8, 0, 7, 1, 6, 2]


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


def test_curriculum_retarget_receipt_replays_public_state() -> None:
    state = CurriculumState(active_K=20, frontier_depth=4, ema_solve_rate=0.4)
    config = CurriculumConfig(k_min=10, k_max=100)
    decision = retarget_curriculum(state, solved_slots=0, validator_capacity=50, config=config)

    assert curriculum_retarget_receipt(
        tempo=7,
        previous_state=state,
        solved_slots=0,
        validator_capacity=50,
        config=config,
        decision=decision,
    ) == {
        "version": "lemma-curriculum-retarget-v1",
        "activation_tempo": 9,
        "previous_active_K": 20,
        "previous_frontier_depth": 4,
        "previous_ema_solve_rate": 0.4,
        "solved_slots": 0,
        "solve_rate": 0.0,
        "validator_capacity": 50,
        "config": {
            "beta": 0.8,
            "low_band": 0.4,
            "high_band": 0.7,
            "k_min": 10,
            "k_max": 100,
            "cost_budget_s": 0.0,
            "base_task_cost_s": 0.0,
            "depth_cost_multiplier": 2.0,
        },
        "next_active_K": 20,
        "next_frontier_depth": 4,
        "next_ema_solve_rate": 0.32000000000000006,
    }


def test_curriculum_tempo_record_ignores_legacy_activation_block() -> None:
    record = CurriculumTempoRecord.from_json(
        json.dumps(
            {
                "tempo": 7,
                "active_K": 3,
                "frontier_depth": 2,
                "ema_solve_rate": 0.4,
                "solved_slots": 1,
                "parked_task_ids": [],
                "action": "hold",
                "variant_stream_requested": False,
                "activation_block": 3240,
            },
            sort_keys=True,
        )
    )

    assert "activation_block" not in record.to_json()


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
    triviality_budget = triviality_budget_receipt(
        (),
        tempo=0,
        config=TrivialityRetargetConfig(genesis_budget_s=5, max_budget_s=5),
    )
    novelty_cache = novelty_cache_from_hashes(("0" * 64,))
    source_pool = source_pool_receipt(
        (mathlib_snapshot.fixture_candidates()[0],),
        source_pool_sha256="4" * 64,
        citation_alpha=0.5,
        citation_weight_cap=64,
        citation_window_tempos=2000,
    )
    metadata: dict[str, object] = {
        "activation_status": "paid",
        "supply_mode": "procedural",
        "tempo": 0,
        "mutation_depth": 2,
        "mutation_chain": [
            {
                "operator": "symm",
                "params": {
                    "rule": "reverse_relation",
                    "relation": "=",
                    "engine": MUTATION_ENGINE,
                },
                "input_hash": "1" * 64,
                "output_hash": "2" * 64,
            },
            {
                "operator": "specialize",
                "params": {
                    "binder": "n",
                    "binder_type": "Nat",
                    "value": "1",
                    "engine": MUTATION_ENGINE,
                },
                "input_hash": "2" * 64,
                "output_hash": "3" * 64,
            },
        ],
        "generation_seed": "tempo-0",
        "drand_round": 10,
        "anchor_block": 360,
        "source_pool_hash": "4" * 64,
        "source_pool_receipt_version": source_pool["version"],
        "source_pool_receipt_sha256": source_pool_receipt_sha256(source_pool),
        "source_pool_source_count": source_pool["source_count"],
        "source_pool_stream_counts": source_pool["source_stream_counts"],
        "source_sampling_version": source_pool["sampling_version"],
        "citation_alpha_basis_points": source_pool["citation_alpha_basis_points"],
        "citation_weight_cap_micros": source_pool["citation_weight_cap_micros"],
        "citation_window_tempos": source_pool["citation_window_tempos"],
        "operator_bundle_version": OPERATOR_BUNDLE_VERSION,
        "operator_bundle_hash": procedural_operator_bundle_hash(),
        "canonical_hash": "6" * 64,
        "kernel_canonical_hash": "6" * 64,
        "kernel_canonical_name": "LemmaProceduralGate.prop_gate",
        "statement_hash": "7" * 64,
        "typechecked": True,
        "prop_gate_passed": True,
        "triviality_checked": True,
        "baseline_solved": False,
        "novelty_status": "passed",
        "license_state": "clean_open",
        "gate_version": GATE_VERSION,
        "gate_runner": "lean",
        "typecheck_reason": "ok",
        "prop_gate_reason": "ok",
        "triviality_stack": ["pytest"],
        "triviality_reason": "baseline_failed",
        "baseline_solver": None,
        "source_task_id": "lemma.mathlib_snapshot.Mathlib.Source.test_true",
        "source_theorem_name": "Mathlib.Source.test_true",
        "source_target_sha256": "8" * 64,
        "source_oracle_checked": True,
        "source_oracle_solved": False,
        "source_oracle_solver": None,
        "source_import_status": "source_theorem_unavailable",
        **novelty_cache.metadata(),
        **triviality_budget.metadata(),
    }
    good = mathlib_snapshot.fixture_candidates()[0].model_copy(
        update={
            "id": "lemma.procedural.depth2",
            "source_stream": "procedural",
            "source_ref": SourceRef(kind="procedural", name="tempo-0-depth2", path="Mathlib/Source.lean"),
            "imports": ("Mathlib.Init",),
            "metadata": metadata,
        }
    )
    good = good.model_copy(
        update={
            "metadata": {
                **good.metadata,
                **slot_weight_receipt_for_candidate(good, import_graph=_import_graph()).metadata(),
            }
        }
    )
    good = good.model_copy(
        update={
            "metadata": {
                **good.metadata,
                "gate_receipt_sha256": procedural_gate_receipt_sha256(good.to_task()),
            }
        }
    )
    depth_one = good.model_copy(
        update={
            "id": "lemma.procedural.depth1",
            "metadata": {**good.metadata, "mutation_depth": 1, "mutation_chain": metadata["mutation_chain"][:1]},
        }
    )

    build = build_procedural_registry_tasks((good, depth_one), seed="procedural", frontier_depth=2)

    assert [task.id for task in build.tasks] == [good.id]
    assert build.tasks[0].source_stream == "procedural"
    assert build.tasks[0].metadata["slot_weight_version"] == "lemma-slot-weight-v5"
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


def test_curriculum_does_not_raise_k_while_advancing_frontier() -> None:
    config = CurriculumConfig(beta=0.0, low_band=0.4, high_band=0.7, k_min=10, k_max=100)
    state = CurriculumState(active_K=20, frontier_depth=2, ema_solve_rate=0.5)

    decision = retarget_curriculum(state, solved_slots=18, validator_capacity=100, config=config)

    assert decision.state.frontier_depth == 3
    assert decision.state.active_K == 20


def test_curriculum_cost_budget_hard_caps_k_at_higher_frontier() -> None:
    config = CurriculumConfig(
        beta=0.0,
        low_band=0.4,
        high_band=0.7,
        k_min=10,
        k_max=100,
        cost_budget_s=100,
        base_task_cost_s=10,
        depth_cost_multiplier=2,
    )
    state = CurriculumState(active_K=10, frontier_depth=1, ema_solve_rate=0.5)

    decision = retarget_curriculum(state, solved_slots=10, validator_capacity=100, config=config)

    assert decision.state.frontier_depth == 2
    assert decision.state.active_K == 2


def test_curriculum_retarget_receipt_exposes_cost_cap_for_replay() -> None:
    config = CurriculumConfig(
        beta=0.0,
        low_band=0.4,
        high_band=0.7,
        k_min=10,
        k_max=100,
        cost_budget_s=100,
        base_task_cost_s=10,
        depth_cost_multiplier=2,
    )
    state = CurriculumState(active_K=10, frontier_depth=1, ema_solve_rate=0.5)
    decision = retarget_curriculum(state, solved_slots=10, validator_capacity=100, config=config)
    receipt = curriculum_retarget_receipt(
        tempo=7,
        previous_state=state,
        solved_slots=10,
        validator_capacity=100,
        config=config,
        decision=decision,
    )

    assert receipt["next_cost_limited_K"] == 2
    assert receipt["next_estimated_task_cost_s"] == 40
    assert receipt["next_active_K"] == 2


def test_triviality_budget_retargets_from_public_burn_history() -> None:
    config = TrivialityRetargetConfig(
        genesis_budget_s=100,
        min_budget_s=10,
        max_budget_s=1000,
        window_tempos=2,
        low_burn_basis_points=4000,
        high_burn_basis_points=7000,
        max_step_basis_points=2500,
    )

    low_burn = triviality_budget_receipt(
        (
            BurnRateRecord(tempo=0, burn_rate_basis_points=1000),
            BurnRateRecord(tempo=1, burn_rate_basis_points=2000),
        ),
        tempo=2,
        config=config,
    )
    high_burn = triviality_budget_receipt(
        (
            BurnRateRecord(tempo=0, burn_rate_basis_points=9000),
            BurnRateRecord(tempo=1, burn_rate_basis_points=10000),
        ),
        tempo=2,
        config=config,
    )

    assert low_burn.budget_s > config.genesis_budget_s
    assert high_burn.budget_s < config.genesis_budget_s
    assert low_burn.inputs["settlement_count"] == 2


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
