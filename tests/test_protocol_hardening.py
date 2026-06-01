"""Lean-only production gates and graph-shaped row metadata."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from bittensor_wallet import Keypair
from lemma.common.config import LemmaSettings
from lemma.corpus import build_corpus_row
from lemma.lean.proof_identity import proof_identity
from lemma.lean.sandbox import VerifyResult
from lemma.problems.base import Problem
from lemma.protocol_invariants import (
    _legacy_procedural_gate_receipt_sha256,
    enforce_production_invariants,
    procedural_gate_receipt_sha256,
    production_supply_rejection_reason,
)
from lemma.scoring import VerificationRecord, score_epoch
from lemma.submissions import build_submission, sign_submission
from lemma.supply.gates import GATE_VERSION
from lemma.supply.import_graph import ImportGraphRow, import_graph_from_rows, write_import_graph_jsonl
from lemma.supply.ingredients import (
    IngredientGateReceipt,
    IngredientManifest,
    IngredientSelectionReceipt,
    build_fixture_ingredient_task,
    build_ingredient_generation_receipt,
    canonical_json_bytes,
    canonical_sha256,
    expected_ingredient_generation_receipt_sha256,
    expected_ingredient_novelty_family_hash,
    ingredient_challenge_seed_sha256,
    ingredient_challenge_slot_seed_sha256,
)
from lemma.supply.novelty import novelty_cache_from_hashes
from lemma.supply.operator_bundle import MUTATION_ENGINE, OPERATOR_BUNDLE_VERSION, procedural_operator_bundle_hash
from lemma.supply.slot_weight import slot_weight_receipt_for_task
from lemma.supply.source_pool import source_pool_receipt, source_pool_receipt_sha256
from lemma.supply.source_pricing import source_pricing_metadata
from lemma.supply.triviality_budget import TrivialityRetargetConfig, triviality_budget_receipt
from lemma.task_activation import task_reward_eligibility
from lemma.task_supply import DEFAULT_TOOLCHAIN, make_task, write_registry
from lemma.tasks import (
    SourceRef,
    Ss58RegistrySignatureVerifier,
    TaskRegistry,
    load_task_registry,
    problem_target_sha256,
    registry_signing_payload,
    task_registry_from_tasks,
)
from lemma.validator import (
    _active_slot_weights,
    active_epoch_seed,
    active_registry_cache_stale,
    active_tasks_for_validation,
    task_registry_for_validation,
    validate_once,
)


def _task(source_license: str = "CC-BY-4.0", *, task_id: str = "lemma.test.true"):
    return make_task(
        task_id=task_id,
        title="True task",
        theorem_name="test_true",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
        source_license=source_license,
        triviality_status="paid_medium",
        metadata={"triviality_checked": True},
    ).model_copy(update={"difficulty_band": "medium"})


def _difficulty_state_jsonl(*rows: dict[str, object]) -> bytes:
    return b"".join(canonical_json_bytes(row) + b"\n" for row in rows)


def _procedural_metadata(
    *,
    mutation_depth: int = 2,
    generation_seed: str = "pytest-depth2",
    tempo: int = 0,
) -> dict[str, object]:
    triviality_budget = triviality_budget_receipt(
        (),
        tempo=tempo,
        config=TrivialityRetargetConfig(genesis_budget_s=5, max_budget_s=5),
    )
    novelty_cache = novelty_cache_from_hashes(("0" * 64,))
    source_pool = source_pool_receipt(
        (_task().model_copy(update={"source_stream": "mathlib_snapshot"}),),
        source_pool_sha256="4" * 64,
        citation_alpha=0.5,
        citation_weight_cap=64,
        citation_window_tempos=2000,
    )
    return {
        "supply_mode": "procedural",
        "tempo": tempo,
        "mutation_depth": mutation_depth,
        "mutation_chain": [
            {
                "operator": "pair-congr",
                "params": {
                    "rule": "pair_congr",
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
        ][:mutation_depth],
        "generation_seed": generation_seed,
        "drand_round": 12,
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
        "novelty_status": "passed",
        "baseline_solved": False,
        "license_state": "clean_open",
        "source_task_id": "lemma.mathlib_snapshot.Mathlib.Source.test_true",
        "source_theorem_name": "Mathlib.Source.test_true",
        "source_target_sha256": "8" * 64,
        "triviality_checked": True,
        "gate_runner": "lean",
        "typecheck_reason": "ok",
        "prop_gate_reason": "ok",
        "triviality_stack": ["pytest"],
        "triviality_reason": "baseline_failed",
        "baseline_solver": None,
        "source_oracle_checked": True,
        "source_oracle_solved": False,
        "source_oracle_solver": None,
        "source_import_status": "source_theorem_unavailable",
        **novelty_cache.metadata(),
        **triviality_budget.metadata(),
    }


def _import_graph():
    return import_graph_from_rows(
        (
            ImportGraphRow(module="Mathlib.Source", imports=("Mathlib.Init",)),
            ImportGraphRow(module="Mathlib", imports=("Mathlib.Init",)),
            ImportGraphRow(module="Mathlib.Init", imports=()),
        )
    )


def _write_import_graph(path) -> None:  # noqa: ANN001
    write_import_graph_jsonl(
        (
            ImportGraphRow(module="Mathlib.Source", imports=("Mathlib.Init",)),
            ImportGraphRow(module="Mathlib", imports=("Mathlib.Init",)),
            ImportGraphRow(module="Mathlib.Init", imports=()),
        ),
        path,
    )


def _production_task(*, mutation_depth: int = 2, generation_seed: str = "pytest-depth2", tempo: int = 0):
    metadata = {
        **_procedural_metadata(mutation_depth=mutation_depth, generation_seed=generation_seed, tempo=tempo),
        "gate_version": GATE_VERSION,
    }
    task = _task().model_copy(
        update={
            "source_stream": "procedural",
            "source_ref": SourceRef(kind="procedural", name="pytest-depth2", path="Mathlib/Source.lean"),
            "imports": ("Mathlib.Init",),
            "metadata": metadata,
        }
    )
    task = task.model_copy(
        update={
            "metadata": {
                **task.metadata,
                **slot_weight_receipt_for_task(task, import_graph=_import_graph()).metadata(),
            }
        }
    )
    return task.model_copy(
        update={"metadata": {**task.metadata, "gate_receipt_sha256": procedural_gate_receipt_sha256(task)}}
    )


def _production_settings(**updates: object) -> LemmaSettings:
    base = {
        "_env_file": None,
        "protocol_mode": "production",
        "task_supply_mode": "procedural",
        "procedural_source_sha256_expected": "4" * 64,
        "procedural_operator_bundle_sha256_expected": procedural_operator_bundle_hash(),
        "enabled_domains": ("lean",),
        "lean_sandbox_network": "none",
        "require_submission_signatures": True,
        "require_commit_reveal": True,
        "require_strong_proof_identity": True,
        "active_seed_mode": "epoch_randomness",
        "active_epoch_randomness_source": "chain_drand",
    }
    return LemmaSettings(**(base | updates))


def _ingredient_manifest_payload(*, mathlib_commit: str = "abc123", recipe_bundle_sha256: str = "a" * 64) -> dict:
    return IngredientManifest(
        schema_version=1,
        mathlib_commit=mathlib_commit,
        lemma_corpus_snapshot_sha256="f" * 64,
        definitions_sha256="a" * 64,
        facts_sha256="1" * 64,
        source_theorems_sha256="2" * 64,
        source_lemmas_sha256="3" * 64,
        compatibility_graph_sha256="4" * 64,
        source_compatibility_sha256="5" * 64,
        definition_compatibility_sha256="6" * 64,
        bridge_catalog_sha256="7" * 64,
        recipe_selectors_sha256="8" * 64,
        recipe_bundle_sha256=recipe_bundle_sha256,
        difficulty_ladder_sha256="9" * 64,
        difficulty_retarget_sha256="b" * 64,
        novelty_policy_sha256="c" * 64,
        shortcut_policy_sha256="d" * 64,
        reserve_selector_policy_sha256="e" * 64,
    ).model_dump(mode="json")


def _ingredient_fixture(
    tmp_path: Path,
    *,
    imports: tuple[str, ...] = ("Mathlib",),
    active_task_count: int = 1,
    epoch_seed: str = "pytest-epoch-seed",
):
    manifest = tmp_path / "manifest.json"
    recipe_sha256 = "a" * 64
    manifest.write_text(
        json.dumps(_ingredient_manifest_payload(recipe_bundle_sha256=recipe_sha256)) + "\n",
        encoding="utf-8",
    )
    difficulty = tmp_path / "difficulty-state.jsonl"
    difficulty.write_bytes(_difficulty_state_jsonl({"tempo": 0, "difficulty_lane": "hard"}))
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()
    repo_commit = "abc123"
    difficulty_sha256 = hashlib.sha256(difficulty.read_bytes()).hexdigest()
    challenge_seed = ingredient_challenge_seed_sha256(
        netuid=0,
        tempo=0,
        epoch_seed=epoch_seed,
        ingredient_manifest_sha256=manifest_sha256,
        recipe_bundle_sha256=recipe_sha256,
        difficulty_state_sha256=difficulty_sha256,
    )
    tasks = []
    for index in range(active_task_count):
        task_id = "lemma.ingredient.true" if index == 0 else f"lemma.ingredient.true_{index}"
        theorem_name = "test_true" if index == 0 else f"test_true_{index}"
        statement = f"theorem {theorem_name} : True := by\n  sorry"
        active_target_sha256 = problem_target_sha256(
            Problem(
                id=task_id,
                theorem_name=theorem_name,
                type_expr="True",
                split="ingredient",
                lean_toolchain=DEFAULT_TOOLCHAIN,
                mathlib_rev="abc123",
                imports=imports,
                extra={"challenge_full": statement},
            )
        )
        selection = IngredientSelectionReceipt(
            selected_selector_id="hard_selector",
            selected_recipe_id="pytest_ingredient_v1",
            selected_definition_ids=("True",),
            selected_fact_ids=("True.intro",),
            difficulty_lane="hard",
            selection_seed_sha256=ingredient_challenge_slot_seed_sha256(
                challenge_seed_sha256=challenge_seed,
                queue_position=index,
                active_K=active_task_count,
            ),
        )
        theorem_statement_sha256 = hashlib.sha256(statement.encode("utf-8")).hexdigest()
        gate_receipt = IngredientGateReceipt(
            schema_version=1,
            receipt_kind="statement_gate",
            active_task_id=task_id,
            active_target_sha256=active_target_sha256,
            theorem_statement_sha256=theorem_statement_sha256,
            ingredient_manifest_sha256=manifest_sha256,
            selection_receipt_sha256=canonical_sha256(selection),
            status="passed",
            runner="fixture-statement-gate",
            checks=("metadata_bound",),
        )
        receipt = build_ingredient_generation_receipt(
            tempo=0,
            epoch_seed=epoch_seed,
            ingredient_manifest_sha256=manifest_sha256,
            lemma_corpus_snapshot_sha256="f" * 64,
            ingredient_repo_commit=repo_commit,
            mathlib_commit="abc123",
            recipe_bundle_sha256=recipe_sha256,
            difficulty_state_sha256=difficulty_sha256,
            selection=selection,
            active_task_id=task_id,
            active_target_sha256=active_target_sha256,
            theorem_statement=statement,
            gate_receipt=gate_receipt,
            shortcut_receipt=gate_receipt.model_copy(
                update={
                    "receipt_kind": "shortcut_gate",
                    "runner": "fixture-shortcut-gate",
                }
            ),
            active_K=active_task_count,
        )
        tasks.append(
            build_fixture_ingredient_task(
                receipt=receipt,
                theorem_name=theorem_name,
                type_expr="True",
                statement=statement,
                imports=imports,
                queue_position=index,
            )
        )
    settings = _production_settings(
        task_supply_mode="ingredient",
        active_task_count=active_task_count,
        ingredient_manifest_json=manifest,
        ingredient_manifest_sha256_expected=manifest_sha256,
        ingredient_repo_commit=repo_commit,
        ingredient_recipe_bundle_sha256_expected=recipe_sha256,
        ingredient_difficulty_state_jsonl=difficulty,
    )
    registry = task_registry_from_tasks(tuple(tasks))
    return settings, registry


def test_active_registry_cache_stale_checks_operator_bundle_hash() -> None:
    registry = TaskRegistry(schema_version=1, tasks=(_production_task(),), sha256="0" * 64, signature_status="verified")
    settings = _production_settings(procedural_operator_bundle_sha256_expected="0" * 64)

    assert active_registry_cache_stale(registry, settings) is True


def test_active_registry_cache_stale_checks_current_operator_bundle_by_default() -> None:
    stale = _production_task().model_copy(
        update={"metadata": {**_production_task().metadata, "operator_bundle_hash": "0" * 64}}
    )
    registry = TaskRegistry(schema_version=1, tasks=(stale,), sha256="0" * 64, signature_status="verified")

    assert active_registry_cache_stale(registry, _production_settings()) is True


def test_active_registry_cache_accepts_half_generation_floor_with_matching_target() -> None:
    tasks = tuple(
        _production_task().model_copy(
            update={
                "id": f"lemma.test.partial_{index}",
                "frontier_depth": 0,
                "metadata": {
                    **_production_task().metadata,
                    "procedural_generation_target_count": 6,
                    "procedural_generation_accepted_count": 3,
                    "procedural_generation_attempt_count": 300,
                    "procedural_generation_attempt_limit": 300,
                },
            }
        )
        for index in range(3)
    )
    registry = TaskRegistry(schema_version=1, tasks=tasks, sha256="0" * 64, signature_status="verified")
    settings = _production_settings(active_task_count=6)

    assert active_registry_cache_stale(registry, settings) is False


def test_active_registry_cache_accepts_partial_generation_before_attempt_budget_exhausted() -> None:
    tasks = tuple(
        _production_task().model_copy(
            update={
                "id": f"lemma.test.partial_early_{index}",
                "frontier_depth": 0,
                "metadata": {
                    **_production_task().metadata,
                    "procedural_generation_target_count": 6,
                    "procedural_generation_accepted_count": 3,
                    "procedural_generation_attempt_count": 16,
                    "procedural_generation_attempt_limit": 300,
                },
            }
        )
        for index in range(3)
    )
    registry = TaskRegistry(schema_version=1, tasks=tasks, sha256="0" * 64, signature_status="verified")
    settings = _production_settings(active_task_count=6)

    assert active_registry_cache_stale(registry, settings) is False


def test_active_registry_cache_rejects_partial_generation_below_floor() -> None:
    tasks = tuple(
        _production_task().model_copy(
            update={
                "id": f"lemma.test.partial_small_{index}",
                "frontier_depth": 0,
                "metadata": {
                    **_production_task().metadata,
                    "procedural_generation_target_count": 6,
                    "procedural_generation_accepted_count": 2,
                },
            }
        )
        for index in range(2)
    )
    registry = TaskRegistry(schema_version=1, tasks=tasks, sha256="0" * 64, signature_status="verified")
    settings = _production_settings(active_task_count=6)

    assert active_registry_cache_stale(registry, settings) is True


def test_active_registry_cache_rejects_partial_generation_for_different_target() -> None:
    task = _production_task().model_copy(
        update={
            "frontier_depth": 0,
            "metadata": {**_production_task().metadata, "procedural_generation_target_count": 4},
        }
    )
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified")
    settings = _production_settings(active_task_count=6)

    assert active_registry_cache_stale(registry, settings) is True


def test_active_registry_cache_stale_checks_gate_version() -> None:
    task = _production_task()
    stale = task.model_copy(update={"metadata": {**task.metadata, "gate_version": "lemma-procedural-gates-old"}})
    registry = TaskRegistry(schema_version=1, tasks=(stale,), sha256="0" * 64, signature_status="verified")

    assert active_registry_cache_stale(registry, _production_settings()) is True


def test_active_registry_cache_stale_checks_missing_procedural_versions() -> None:
    metadata = {
        key: value
        for key, value in _production_task().metadata.items()
        if key not in {"gate_version", "operator_bundle_version", "source_sampling_version"}
    }
    stale = _production_task().model_copy(update={"metadata": metadata})
    registry = TaskRegistry(schema_version=1, tasks=(stale,), sha256="0" * 64, signature_status="verified")

    assert active_registry_cache_stale(registry, _production_settings()) is True


def test_active_registry_cache_stale_checks_yield_history_hash(tmp_path: Path) -> None:
    history = tmp_path / "yield-history.jsonl"
    history.write_text(json.dumps({"accepted_source_families": {"Mathlib/Old.lean": 1}}) + "\n", encoding="utf-8")
    task = _production_task()
    stale = task.model_copy(
        update={
            "metadata": {
                **task.metadata,
                "yield_history_version": "lemma-procedural-yield-history-v1",
                "yield_history_sha256": "0" * 64,
                "yield_history_entries": 1,
            }
        }
    )
    registry = TaskRegistry(schema_version=1, tasks=(stale,), sha256="0" * 64, signature_status="verified")

    assert active_registry_cache_stale(registry, _production_settings(procedural_yield_history_jsonl=history)) is True


def test_active_registry_cache_stale_checks_ingredient_contract(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    assert active_registry_cache_stale(registry, settings) is False

    difficulty = settings.ingredient_difficulty_state_jsonl
    assert difficulty is not None
    difficulty.write_bytes(_difficulty_state_jsonl({"tempo": 1, "difficulty_lane": "frontier"}))

    assert active_registry_cache_stale(registry, settings) is True


def test_production_supply_rejects_malformed_yield_history_metadata() -> None:
    task = _production_task()
    bad = task.model_copy(
        update={
            "metadata": {
                **task.metadata,
                "yield_history_version": "lemma-procedural-yield-history-v1",
                "yield_history_sha256": "not-a-sha",
                "yield_history_entries": 1,
            }
        }
    )

    assert production_supply_rejection_reason(bad) == "yield_history_sha256"


def _proof() -> str:
    return "import Mathlib\n\nnamespace Submission\n\ntheorem test_true : True := by\n  trivial\n\nend Submission\n"


def test_normalized_script_identity_is_weak_and_stable_across_whitespace() -> None:
    first = proof_identity(proof_sha256="a", proof_script="by\n  trivial")
    second = proof_identity(proof_sha256="b", proof_script="by trivial")

    assert first.value == second.value
    assert first.source == "normalized_script_sha256"
    assert first.strength == "weak"


def test_strong_identity_makes_useful_graph_row_full_reward_eligible() -> None:
    task = _task()
    submission = build_submission(task, solver_hotkey="hk", proof_script=_proof())

    row = build_corpus_row(
        task,
        submission,
        VerifyResult(passed=True, reason="ok", proof_term_hash="term-hash"),
        validator_hotkey="vhk",
        rewarded=True,
    )

    assert row.proof_identity_source == "proof_term_hash"
    assert row.proof_identity_strength == "strong"
    assert row.full_reward_eligible is True
    assert row.quality.useful_verified_row is True
    assert row.graph is not None
    assert {"task", "proof", "identity", "source", "verifier", "solver", "validator"} <= set(row.graph.node_ids)
    assert row.dependencies.mathlib_imports == ("Mathlib",)


def test_structural_fingerprint_counts_as_medium_identity() -> None:
    task = _task()
    submission = build_submission(task, solver_hotkey="hk", proof_script=_proof())

    row = build_corpus_row(
        task,
        submission,
        VerifyResult(passed=True, reason="ok", structural_fingerprint="structural-hash"),
        validator_hotkey="vhk",
        rewarded=True,
    )

    assert row.proof_identity == "structural-hash"
    assert row.proof_identity_source == "structural_fingerprint"
    assert row.proof_identity_strength == "medium"
    assert row.full_reward_eligible is False


def test_weak_identity_row_can_be_valid_without_full_reward_eligibility() -> None:
    task = _task()
    submission = build_submission(task, solver_hotkey="hk", proof_script=_proof())

    row = build_corpus_row(
        task,
        submission,
        VerifyResult(passed=True, reason="ok"),
        validator_hotkey="vhk",
        rewarded=True,
    )

    assert row.proof_identity_source == "normalized_script_sha256"
    assert row.proof_identity_strength == "weak"
    assert row.full_reward_eligible is False
    assert row.quality.useful_verified_row is False


def test_license_gate_blocks_unknown_paid_activation() -> None:
    eligibility = task_reward_eligibility(_task(source_license="unknown"))

    assert eligibility.eligible is False
    assert eligibility.reason == "license_state:unknown"


def test_production_scoring_requires_strong_identity() -> None:
    result = score_epoch(
        [
            VerificationRecord(
                task_id="task-1",
                solver_hotkey="hk-a",
                passed=True,
                proof_sha256="a",
                proof_identity="weak",
                proof_identity_source="normalized_script_sha256",
            ),
            VerificationRecord(
                task_id="task-2",
                solver_hotkey="hk-b",
                passed=True,
                proof_sha256="b",
                proof_term_hash="strong",
                proof_identity="strong",
                proof_identity_source="proof_term_hash",
            ),
        ],
        active_task_count=2,
        require_strong_identity_for_reward=True,
    )

    assert result.winners == {"task-2": "hk-b"}
    assert result.scores == {"hk-b": 0.5}
    events = [(event.solver_hotkey, event.rewarded, event.reward_ineligibility_reason) for event in result.score_events]
    assert events == [
        ("hk-a", False, "weak_proof_identity"),
        ("hk-b", True, ""),
    ]


def test_registry_signature_verifies_canonical_payload() -> None:
    task = _task()
    keypair = Keypair.create_from_uri("//LemmaRegistrySigner")
    payload = {
        "schema_version": 1,
        "tasks": [task.model_dump(mode="json", exclude_none=True)],
    }
    signature = "0x" + keypair.sign(registry_signing_payload(payload)).hex()
    raw = {
        **payload,
        "signed_by": keypair.ss58_address,
        "signature": signature,
    }
    registry = load_task_registry(
        json.dumps(raw, indent=2, sort_keys=True).encode(),
        signature_verifier=Ss58RegistrySignatureVerifier(),
    )

    assert registry.signature_status == "verified"


def test_production_validator_does_not_reward_weak_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "lemma.validator.resolve_active_epoch_randomness",
        lambda settings, *, tempo: "pytest-anchor-block-and-drand",
    )
    settings = _production_settings(
        operator_data_dir=tmp_path / "operator",
        corpus_output_dir=tmp_path / "corpus",
    )
    task = _production_task(generation_seed=active_epoch_seed(settings, tempo=0))
    keypair = Keypair.create_from_uri("//LemmaProdMiner")
    submission = sign_submission(
        build_submission(
            task,
            solver_hotkey=keypair.ss58_address,
            proof_script=_proof(),
        ).model_copy(
            update={
                "timelock_ciphertext": "ciphertext",
                "drand_round": 10,
                "commit_block": 42,
                "commit_extrinsic_hash": "0xabc",
            }
        ),
        keypair,
    )
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified")

    result = validate_once(
        settings,
        [submission],
        registry=registry,
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        tempo=0,
        no_set_weights=True,
        chain_authenticated_keys=frozenset({(task.id, submission.solver_hotkey, submission.proof_sha256)}),
    )

    assert result.score.credits == {}
    assert result.score.score_events[0].reward_ineligibility_reason == "weak_proof_identity"
    assert result.corpus_rows[0].rewarded is False


def test_production_validator_rejects_direct_signed_submission_without_bucket_auth(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "lemma.validator.resolve_active_epoch_randomness",
        lambda settings, *, tempo: "pytest-anchor-block-and-drand",
    )
    settings = _production_settings(
        operator_data_dir=tmp_path / "operator",
        corpus_output_dir=tmp_path / "corpus",
    )
    task = _production_task(generation_seed=active_epoch_seed(settings, tempo=0))
    keypair = Keypair.create_from_uri("//LemmaDirectProdMiner")
    submission = sign_submission(
        build_submission(task, solver_hotkey=keypair.ss58_address, proof_script=_proof()).model_copy(
            update={
                "timelock_ciphertext": "ciphertext",
                "drand_round": 10,
                "commit_block": 42,
                "commit_extrinsic_hash": "0xabc",
            }
        ),
        keypair,
    )
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified")

    result = validate_once(
        settings,
        [submission],
        registry=registry,
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        tempo=0,
        no_set_weights=True,
    )

    assert result.verification_records == ()
    assert result.score.scores == {}


def test_production_mode_requires_procedural_supply_mode() -> None:
    task = _production_task()
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="metadata_only")
    settings = _production_settings(
        task_supply_mode="registry",
        task_registry_sha256_expected="0" * 64,
    )

    with pytest.raises(RuntimeError, match="LEMMA_TASK_SUPPLY_MODE=procedural"):
        enforce_production_invariants(settings, registry)


def test_ingredient_production_mode_accepts_one_pinned_task(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)

    enforce_production_invariants(settings, registry)


def test_ingredient_production_mode_accepts_dynamic_active_k_registry(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path, active_task_count=2)

    enforce_production_invariants(settings, registry)


def test_ingredient_production_mode_requires_registry_count_to_match_active_k(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)

    with pytest.raises(RuntimeError, match="active task count mismatch"):
        enforce_production_invariants(settings.model_copy(update={"active_task_count": 2}), registry)


def test_ingredient_production_mode_checks_each_active_task(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path, active_task_count=2)
    drifted = registry.tasks[1].model_copy(update={"source_stream": "human_curated"})

    with pytest.raises(RuntimeError, match="source_stream=ingredient"):
        enforce_production_invariants(
            settings,
            task_registry_from_tasks((registry.tasks[0], drifted)),
        )


def test_ingredient_production_mode_rejects_duplicate_slot_seed(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path, active_task_count=2)
    duplicate_seed = registry.tasks[0].metadata["selection_seed_sha256"]
    drifted = registry.tasks[1].model_copy(
        update={
            "metadata": {
                **registry.tasks[1].metadata,
                "selection_seed_sha256": duplicate_seed,
            }
        }
    )

    with pytest.raises(RuntimeError, match="selection seed duplicated"):
        enforce_production_invariants(
            settings,
            task_registry_from_tasks((registry.tasks[0], drifted)),
        )


def test_ingredient_production_mode_rejects_registry_schema_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    drifted = TaskRegistry(
        schema_version=2,
        tasks=registry.tasks,
        sha256=registry.sha256,
        signature_status=registry.signature_status,
    )

    with pytest.raises(RuntimeError, match="registry schema_version mismatch"):
        enforce_production_invariants(settings, drifted)


def test_ingredient_production_mode_rejects_registry_side_channel(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    drifted = TaskRegistry(
        schema_version=registry.schema_version,
        tasks=registry.tasks,
        sha256=registry.sha256,
        signature_status=registry.signature_status,
        created_at="2026-01-01T00:00:00Z",
    )

    with pytest.raises(RuntimeError, match="registry has local side channel"):
        enforce_production_invariants(settings, drifted)


@pytest.mark.parametrize("sha256", ["0" * 64, "not-a-sha"])
def test_ingredient_production_mode_rejects_registry_sha_placeholder(tmp_path: Path, sha256: str) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    drifted = TaskRegistry(
        schema_version=registry.schema_version,
        tasks=registry.tasks,
        sha256=sha256,
        signature_status=registry.signature_status,
    )

    with pytest.raises(RuntimeError, match="non-placeholder registry sha256"):
        enforce_production_invariants(settings, drifted)


def test_ingredient_production_mode_rejects_unknown_registry_signature_status(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    drifted = TaskRegistry(
        schema_version=registry.schema_version,
        tasks=registry.tasks,
        sha256=registry.sha256,
        signature_status="bogus",
    )

    with pytest.raises(RuntimeError, match="registry signature status mismatch"):
        enforce_production_invariants(settings, drifted)


@pytest.mark.parametrize(
    "updates",
    [
        {"signed_by": "fixture-signer", "signature": "fixture-signature", "signature_status": "metadata_only"},
        {"signed_by": "fixture-signer", "signature": "fixture-signature", "signature_status": "unsigned"},
    ],
)
def test_ingredient_production_mode_rejects_unverified_registry_signature(
    tmp_path: Path, updates: dict[str, str]
) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    drifted = TaskRegistry(
        schema_version=registry.schema_version,
        tasks=registry.tasks,
        sha256=registry.sha256,
        **updates,
    )

    with pytest.raises(RuntimeError, match="registry signature unverified"):
        enforce_production_invariants(settings, drifted)


@pytest.mark.parametrize(
    "updates",
    [
        {"signature_status": "verified"},
        {"signed_by": "fixture-signer", "signature_status": "verified"},
        {"signature": "fixture-signature", "signature_status": "verified"},
        {"signed_by": "", "signature": "fixture-signature", "signature_status": "verified"},
        {"signed_by": " fixture-signer ", "signature": "fixture-signature", "signature_status": "verified"},
        {"signed_by": "fixture-signer", "signature": " fixture-signature ", "signature_status": "verified"},
    ],
)
def test_ingredient_production_mode_rejects_incomplete_verified_registry_signature(
    tmp_path: Path, updates: dict[str, str]
) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    drifted = TaskRegistry(
        schema_version=registry.schema_version,
        tasks=registry.tasks,
        sha256=registry.sha256,
        **updates,
    )

    with pytest.raises(RuntimeError, match="registry verified signature metadata missing"):
        enforce_production_invariants(settings, drifted)


def test_ingredient_production_mode_rejects_manifest_hash_mismatch(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)

    with pytest.raises(RuntimeError, match="manifest sha256 mismatch"):
        enforce_production_invariants(
            settings.model_copy(update={"ingredient_manifest_sha256_expected": "0" * 64}),
            registry,
        )


def test_ingredient_production_mode_rejects_symlink_manifest_file(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    manifest = settings.ingredient_manifest_json
    assert manifest is not None
    external_manifest = tmp_path / "manifest.external.json"
    external_manifest.write_bytes(manifest.read_bytes())
    manifest.unlink()
    manifest.symlink_to(external_manifest)

    with pytest.raises(RuntimeError, match="manifest path invalid"):
        enforce_production_invariants(settings, registry)


def test_ingredient_production_mode_rejects_incomplete_manifest(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    manifest = settings.ingredient_manifest_json
    assert manifest is not None
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mathlib_commit": "abc123",
                "recipe_bundle_sha256": settings.ingredient_recipe_bundle_sha256_expected,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()

    with pytest.raises(RuntimeError, match="valid ingredient manifest schema"):
        enforce_production_invariants(
            settings.model_copy(update={"ingredient_manifest_sha256_expected": manifest_sha256}),
            registry,
        )


def test_ingredient_production_mode_rejects_unknown_manifest_field(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    manifest = settings.ingredient_manifest_json
    assert manifest is not None
    payload = _ingredient_manifest_payload(recipe_bundle_sha256=str(settings.ingredient_recipe_bundle_sha256_expected))
    payload["operator_note"] = "pytest"
    manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()

    with pytest.raises(RuntimeError, match="valid ingredient manifest schema"):
        enforce_production_invariants(
            settings.model_copy(update={"ingredient_manifest_sha256_expected": manifest_sha256}),
            registry,
        )


def test_ingredient_production_mode_rejects_manifest_without_corpus_snapshot(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    manifest = settings.ingredient_manifest_json
    assert manifest is not None
    payload = _ingredient_manifest_payload(recipe_bundle_sha256=str(settings.ingredient_recipe_bundle_sha256_expected))
    payload.pop("lemma_corpus_snapshot_sha256")
    manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()

    with pytest.raises(RuntimeError, match="lemma corpus snapshot sha256"):
        enforce_production_invariants(
            settings.model_copy(update={"ingredient_manifest_sha256_expected": manifest_sha256}),
            registry,
        )


def test_ingredient_production_mode_rejects_corpus_snapshot_hash_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    manifest = settings.ingredient_manifest_json
    assert manifest is not None
    payload = _ingredient_manifest_payload(recipe_bundle_sha256=str(settings.ingredient_recipe_bundle_sha256_expected))
    payload["lemma_corpus_snapshot_sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()

    with pytest.raises(RuntimeError, match="valid ingredient manifest schema"):
        enforce_production_invariants(
            settings.model_copy(update={"ingredient_manifest_sha256_expected": manifest_sha256}),
            registry,
        )


def test_ingredient_production_mode_rejects_manifest_component_hash_placeholder(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    manifest = settings.ingredient_manifest_json
    assert manifest is not None
    payload = _ingredient_manifest_payload(recipe_bundle_sha256=str(settings.ingredient_recipe_bundle_sha256_expected))
    payload["definitions_sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()

    with pytest.raises(RuntimeError, match="valid ingredient manifest schema"):
        enforce_production_invariants(
            settings.model_copy(update={"ingredient_manifest_sha256_expected": manifest_sha256}),
            registry,
        )


def test_ingredient_production_mode_rejects_snapshot_hash_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    manifest = settings.ingredient_manifest_json
    assert manifest is not None
    payload = _ingredient_manifest_payload(recipe_bundle_sha256=str(settings.ingredient_recipe_bundle_sha256_expected))
    payload["definitions_sha256"] = "f" * 64
    manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()

    with pytest.raises(RuntimeError, match="task manifest sha256 mismatch"):
        enforce_production_invariants(
            settings.model_copy(update={"ingredient_manifest_sha256_expected": manifest_sha256}),
            registry,
        )


def test_ingredient_production_mode_rejects_task_corpus_snapshot_hash_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, "lemma_corpus_snapshot_sha256": "0" * 64}})

    with pytest.raises(RuntimeError, match="corpus snapshot sha256 mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_manifest_mathlib_commit_mismatch(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    manifest = settings.ingredient_manifest_json
    assert manifest is not None
    manifest.write_text(
        json.dumps(
            _ingredient_manifest_payload(
                mathlib_commit="def456",
                recipe_bundle_sha256=str(settings.ingredient_recipe_bundle_sha256_expected),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()

    with pytest.raises(RuntimeError, match="manifest mathlib commit mismatch"):
        enforce_production_invariants(
            settings.model_copy(update={"ingredient_manifest_sha256_expected": manifest_sha256}),
            registry,
        )


def test_ingredient_production_mode_rejects_whitespace_manifest_mathlib_commit(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    manifest = settings.ingredient_manifest_json
    assert manifest is not None
    payload = _ingredient_manifest_payload(
        recipe_bundle_sha256=str(settings.ingredient_recipe_bundle_sha256_expected),
    )
    payload["mathlib_commit"] = " abc123 "
    manifest.write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()

    with pytest.raises(RuntimeError, match="valid ingredient manifest schema"):
        enforce_production_invariants(
            settings.model_copy(update={"ingredient_manifest_sha256_expected": manifest_sha256}),
            registry,
        )


def test_ingredient_production_mode_rejects_placeholder_manifest_mathlib_commit(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    manifest = settings.ingredient_manifest_json
    assert manifest is not None
    payload = _ingredient_manifest_payload(
        recipe_bundle_sha256=str(settings.ingredient_recipe_bundle_sha256_expected),
    )
    payload["mathlib_commit"] = "0" * 6
    manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()

    with pytest.raises(RuntimeError, match="valid ingredient manifest schema"):
        enforce_production_invariants(
            settings.model_copy(update={"ingredient_manifest_sha256_expected": manifest_sha256}),
            registry,
        )


def test_ingredient_production_mode_rejects_whitespace_repo_commit_setting(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)

    with pytest.raises(RuntimeError, match="requires LEMMA_INGREDIENT_REPO_COMMIT"):
        enforce_production_invariants(settings.model_copy(update={"ingredient_repo_commit": " abc123 "}), registry)


def test_ingredient_production_mode_rejects_manifest_recipe_bundle_mismatch(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    manifest = settings.ingredient_manifest_json
    assert manifest is not None
    manifest.write_text(
        json.dumps(_ingredient_manifest_payload(recipe_bundle_sha256="f" * 64))
        + "\n",
        encoding="utf-8",
    )
    manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()

    with pytest.raises(RuntimeError, match="manifest recipe bundle sha256 mismatch"):
        enforce_production_invariants(
            settings.model_copy(update={"ingredient_manifest_sha256_expected": manifest_sha256}),
            registry,
        )


def test_ingredient_production_mode_rejects_difficulty_state_mismatch(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    difficulty = settings.ingredient_difficulty_state_jsonl
    assert difficulty is not None
    difficulty.write_bytes(_difficulty_state_jsonl({"tempo": 1, "difficulty_lane": "frontier"}))

    with pytest.raises(RuntimeError, match="difficulty state sha256 mismatch"):
        enforce_production_invariants(settings, registry)


def test_ingredient_production_mode_rejects_symlink_difficulty_state_file(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    difficulty = settings.ingredient_difficulty_state_jsonl
    assert difficulty is not None
    external_difficulty = tmp_path / "difficulty-state.external.jsonl"
    external_difficulty.write_bytes(difficulty.read_bytes())
    difficulty.unlink()
    difficulty.symlink_to(external_difficulty)

    with pytest.raises(RuntimeError, match="difficulty state path invalid"):
        enforce_production_invariants(settings, registry)


def test_ingredient_production_mode_rejects_empty_difficulty_state(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    difficulty = settings.ingredient_difficulty_state_jsonl
    assert difficulty is not None
    difficulty.write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="nonempty LEMMA_INGREDIENT_DIFFICULTY_STATE_JSONL"):
        enforce_production_invariants(settings, registry)


def test_ingredient_production_mode_rejects_invalid_difficulty_state_jsonl(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    difficulty = settings.ingredient_difficulty_state_jsonl
    assert difficulty is not None
    difficulty.write_text("not-json\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="valid ingredient difficulty state JSONL"):
        enforce_production_invariants(settings, registry)


def test_ingredient_production_mode_rejects_difficulty_state_without_active_lane(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    difficulty = settings.ingredient_difficulty_state_jsonl
    assert difficulty is not None
    difficulty.write_bytes(_difficulty_state_jsonl({"tempo": 0, "difficulty_lane": "easy"}))
    drifted = task.model_copy(
        update={
            "metadata": {
                **task.metadata,
                "difficulty_state_sha256": hashlib.sha256(difficulty.read_bytes()).hexdigest(),
            }
        }
    )
    receipt = expected_ingredient_generation_receipt_sha256(drifted)
    drifted = drifted.model_copy(
        update={
            "metadata": {
                **drifted.metadata,
                "generation_receipt_sha256": receipt,
            }
        }
    )

    with pytest.raises(RuntimeError, match="difficulty state missing active tempo/lane"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_bool_difficulty_state_tempo(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    difficulty = settings.ingredient_difficulty_state_jsonl
    assert difficulty is not None
    difficulty.write_bytes(_difficulty_state_jsonl({"tempo": False, "difficulty_lane": "hard"}))
    drifted = task.model_copy(
        update={
            "metadata": {
                **task.metadata,
                "difficulty_state_sha256": hashlib.sha256(difficulty.read_bytes()).hexdigest(),
            }
        }
    )
    receipt = expected_ingredient_generation_receipt_sha256(drifted)
    drifted = drifted.model_copy(
        update={
            "metadata": {
                **drifted.metadata,
                "generation_receipt_sha256": receipt,
            }
        }
    )

    with pytest.raises(RuntimeError, match="difficulty state row malformed"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_malformed_extra_difficulty_state_row(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    difficulty = settings.ingredient_difficulty_state_jsonl
    assert difficulty is not None
    difficulty.write_bytes(
        _difficulty_state_jsonl(
            {"tempo": 0, "difficulty_lane": "hard"},
            {"tempo": 1, "difficulty_lane": " hard "},
        )
    )
    drifted = task.model_copy(
        update={
            "metadata": {
                **task.metadata,
                "difficulty_state_sha256": hashlib.sha256(difficulty.read_bytes()).hexdigest(),
            }
        }
    )
    receipt = expected_ingredient_generation_receipt_sha256(drifted)
    drifted = drifted.model_copy(
        update={
            "metadata": {
                **drifted.metadata,
                "generation_receipt_sha256": receipt,
            }
        }
    )

    with pytest.raises(RuntimeError, match="difficulty state row malformed"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_unknown_difficulty_state_field(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    difficulty = settings.ingredient_difficulty_state_jsonl
    assert difficulty is not None
    difficulty.write_bytes(_difficulty_state_jsonl({"tempo": 0, "difficulty_lane": "hard", "generated_at": "local"}))
    drifted = task.model_copy(
        update={
            "metadata": {
                **task.metadata,
                "difficulty_state_sha256": hashlib.sha256(difficulty.read_bytes()).hexdigest(),
            }
        }
    )
    receipt = expected_ingredient_generation_receipt_sha256(drifted)
    drifted = drifted.model_copy(
        update={
            "metadata": {
                **drifted.metadata,
                "generation_receipt_sha256": receipt,
            }
        }
    )

    with pytest.raises(RuntimeError, match="difficulty state row malformed"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_ambiguous_active_difficulty_state(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    difficulty = settings.ingredient_difficulty_state_jsonl
    assert difficulty is not None
    difficulty.write_bytes(
        _difficulty_state_jsonl(
            {"tempo": 0, "difficulty_lane": "hard"},
            {"tempo": 0, "difficulty_lane": "easy"},
        )
    )
    drifted = task.model_copy(
        update={
            "metadata": {
                **task.metadata,
                "difficulty_state_sha256": hashlib.sha256(difficulty.read_bytes()).hexdigest(),
            }
        }
    )
    receipt = expected_ingredient_generation_receipt_sha256(drifted)
    drifted = drifted.model_copy(
        update={
            "metadata": {
                **drifted.metadata,
                "generation_receipt_sha256": receipt,
            }
        }
    )

    with pytest.raises(RuntimeError, match="difficulty state tempo duplicated"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_procedural_registry(tmp_path: Path) -> None:
    settings, _registry = _ingredient_fixture(tmp_path)
    registry = TaskRegistry(schema_version=1, tasks=(_production_task(),), sha256="0" * 64, signature_status="verified")

    with pytest.raises(RuntimeError, match="source_stream=ingredient"):
        enforce_production_invariants(settings, registry)


def test_ingredient_production_mode_requires_reward_eligible_task(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0].model_copy(update={"activation_status": "benchmark"})

    with pytest.raises(RuntimeError, match="reward-eligible task"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_source_license_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0].model_copy(update={"source_license": "MIT"})

    with pytest.raises(RuntimeError, match="source license mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_task_version_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0].model_copy(update={"task_version": 2})

    with pytest.raises(RuntimeError, match="task_version mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_title(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0].model_copy(update={"title": "Generated List Length"})

    with pytest.raises(RuntimeError, match="task title invalid"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_triviality_status_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0].model_copy(update={"triviality_status": "paid_medium"})

    with pytest.raises(RuntimeError, match="triviality status mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified"),
        )


@pytest.mark.parametrize("update", [{"active_epoch": 1}, {"expires_epoch": 2}])
def test_ingredient_production_mode_rejects_lifecycle_window_drift(
    tmp_path: Path, update: dict[str, int]
) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0].model_copy(update=update)

    with pytest.raises(RuntimeError, match="lifecycle window mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_created_at_block_metadata(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, "created_at_block": 1}})

    with pytest.raises(RuntimeError, match="lifecycle window mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


@pytest.mark.parametrize("key", ["created_at", "generated_at", "generation_seed", "local_seed"])
def test_ingredient_production_mode_rejects_local_side_channel_metadata(tmp_path: Path, key: str) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, key: "private"}})

    with pytest.raises(RuntimeError, match="metadata has local side channel"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_unknown_metadata_field(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, "operator_note": "pytest"}})

    with pytest.raises(RuntimeError, match="metadata schema mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


@pytest.mark.parametrize("key,value", [("source_license", "MIT"), ("task_version", 2), ("activation_status", "paid")])
def test_ingredient_production_mode_rejects_task_field_metadata_shadow(
    tmp_path: Path, key: str, value: object
) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, key: value}})

    with pytest.raises(RuntimeError, match="metadata shadows task field"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_submission_policy_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0].model_copy(update={"policy": "strict_envelope"})

    with pytest.raises(RuntimeError, match="restricted_helpers submission policy"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified"),
        )


@pytest.mark.parametrize("update", [{"verifier_id": "other"}, {"verifier_version": "other"}])
def test_ingredient_production_mode_rejects_verifier_identity_drift(
    tmp_path: Path, update: dict[str, str]
) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0].model_copy(update=update)

    with pytest.raises(RuntimeError, match="verifier identity mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_lean_toolchain_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0].model_copy(update={"lean_toolchain": "leanprover/lean4:v4.29.0"})

    with pytest.raises(RuntimeError, match="lean toolchain mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_accepts_public_mathlib_imports(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path, imports=("Mathlib", "Mathlib.Data.Nat.Basic"))

    enforce_production_invariants(settings, registry)


def test_ingredient_production_mode_checks_nonfirst_task_envelope(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path, active_task_count=2)
    drifted = registry.tasks[1].model_copy(update={"source_license": "MIT"})

    with pytest.raises(RuntimeError, match="source license mismatch"):
        enforce_production_invariants(
            settings,
            task_registry_from_tasks((registry.tasks[0], drifted)),
        )


@pytest.mark.parametrize(
    "imports",
    [
        ("Private.OperatorHints",),
        ("Mathlib.Data.Nat.Basic", "Mathlib"),
        ("Mathlib", "Mathlib"),
    ],
)
def test_ingredient_production_mode_rejects_invalid_import_envelope(
    tmp_path: Path, imports: tuple[str, ...]
) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0].model_copy(update={"imports": imports})

    with pytest.raises(RuntimeError, match="import envelope invalid"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified"),
        )


@pytest.mark.parametrize("update", [{"theorem_name": "other_true"}, {"type_expr": "False"}])
def test_ingredient_production_mode_rejects_theorem_header_drift(
    tmp_path: Path, update: dict[str, str]
) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0].model_copy(update=update)

    with pytest.raises(RuntimeError, match="theorem header mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_theorem_extra_declaration(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    task = task.model_copy(update={"statement": f"{task.statement}\n\naxiom hidden_hint : False"})

    with pytest.raises(RuntimeError, match="theorem statement invalid"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_submission_stub_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0].model_copy(update={"submission_stub": "theorem test_true : True := by\n  sorry\n"})

    with pytest.raises(RuntimeError, match="submission stub mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_task_outside_active_frontier(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0].model_copy(update={"queue_depth": settings.frontier_depth + 1})

    with pytest.raises(RuntimeError, match="outside active frontier"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_frontier_depth_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0].model_copy(update={"frontier_depth": settings.frontier_depth + 1})

    with pytest.raises(RuntimeError, match="frontier depth mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_queue_position_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0].model_copy(update={"queue_position": 1})

    with pytest.raises(RuntimeError, match="queue position mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_source_ref_commit_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"source_ref": task.source_ref.model_copy(update={"commit": "different"})})

    with pytest.raises(RuntimeError, match="source_ref commit mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_source_ref_path_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"source_ref": task.source_ref.model_copy(update={"path": "local.lean"})})

    with pytest.raises(RuntimeError, match="source_ref url/path"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_selection_metadata_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, "ingredient_ids": ["True"]}})

    with pytest.raises(RuntimeError, match="ingredient metadata mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_whitespace_selected_ingredient_ids(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    metadata = {
        **task.metadata,
        "fact_ids": [" True.intro "],
        "ingredient_ids": ["True", " True.intro "],
    }
    drifted = task.model_copy(update={"metadata": metadata})

    with pytest.raises(RuntimeError, match="ingredient metadata malformed"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_requires_selected_ingredients(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    metadata = {
        **task.metadata,
        "definition_ids": [],
        "fact_ids": [],
        "bridge_ids": [],
        "ingredient_ids": [],
        "ingredient_count": 0,
    }
    drifted = task.model_copy(update={"metadata": metadata})

    with pytest.raises(RuntimeError, match="selected ingredients missing"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_duplicate_selected_ingredients(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    metadata = {
        **task.metadata,
        "fact_ids": ["True"],
        "bridge_ids": [],
        "ingredient_ids": ["True", "True"],
        "ingredient_count": 2,
    }
    drifted = task.model_copy(update={"metadata": metadata})
    receipt = expected_ingredient_generation_receipt_sha256(drifted)
    drifted = drifted.model_copy(
        update={
            "metadata": {
                **drifted.metadata,
                "novelty_family_hash": expected_ingredient_novelty_family_hash(drifted),
                "generation_receipt_sha256": receipt,
            }
        }
    )

    with pytest.raises(RuntimeError, match="selected ingredients duplicated"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_requires_selected_fact_ingredients(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    metadata = {
        **task.metadata,
        "fact_ids": [],
        "ingredient_ids": ["True"],
        "ingredient_count": 1,
    }
    drifted = task.model_copy(update={"metadata": metadata})
    receipt = expected_ingredient_generation_receipt_sha256(drifted)
    drifted = drifted.model_copy(
        update={
            "metadata": {
                **drifted.metadata,
                "novelty_family_hash": expected_ingredient_novelty_family_hash(drifted),
                "generation_receipt_sha256": receipt,
            }
        }
    )

    with pytest.raises(RuntimeError, match="selected facts missing"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_difficulty_lane_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0].model_copy(update={"difficulty_band": "medium"})

    with pytest.raises(RuntimeError, match="difficulty lane mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_float_task_tempo(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, "tempo": 0.0}})

    with pytest.raises(RuntimeError, match="task tempo mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_float_active_k(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, "active_K": 1.0}})

    with pytest.raises(RuntimeError, match="active_K mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_ingredient_count_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, "ingredient_count": 99}})

    with pytest.raises(RuntimeError, match="ingredient count mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_float_ingredient_count(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    metadata = {
        **task.metadata,
        "ingredient_count": 2.0,
    }
    drifted = task.model_copy(update={"metadata": metadata})

    with pytest.raises(RuntimeError, match="ingredient count mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_hidden_lemma_count_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, "hidden_lemma_count": 1}})

    with pytest.raises(RuntimeError, match="hidden lemma count mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_float_hidden_lemma_count(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, "hidden_lemma_count": 0.0}})

    with pytest.raises(RuntimeError, match="hidden lemma count mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_placeholder_epoch_seed(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, "epoch_seed_sha256": "0" * 64}})

    with pytest.raises(RuntimeError, match="epoch seed placeholder"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_placeholder_selection_seed(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, "selection_seed_sha256": "0" * 64}})

    with pytest.raises(RuntimeError, match="selection seed placeholder"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


@pytest.mark.parametrize(
    "selected_parameters",
    [[], {" limit": "3"}, {"private/path": "2"}, {"Nat": "02"}, {"Bool": "yes"}, {"limit": object()}],
)
def test_ingredient_production_mode_rejects_malformed_selected_parameters(
    tmp_path: Path, selected_parameters: object
) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, "selected_parameters": selected_parameters}})

    with pytest.raises(RuntimeError, match="selected parameters malformed"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_generation_receipt_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, "generation_receipt_sha256": "0" * 64}})

    with pytest.raises(RuntimeError, match="generation receipt mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


@pytest.mark.parametrize(
    ("receipt_key", "message"),
    [("gate_receipt_sha256", "gate receipt placeholder"), ("shortcut_receipt_sha256", "shortcut receipt placeholder")],
)
def test_ingredient_production_mode_rejects_placeholder_check_receipts(
    tmp_path: Path,
    receipt_key: str,
    message: str,
) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    metadata = {**task.metadata, receipt_key: "0" * 64}
    drifted = task.model_copy(update={"metadata": metadata})

    with pytest.raises(RuntimeError, match=message):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_task_id_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0].model_copy(update={"id": "lemma.ingredient.other"})

    with pytest.raises(RuntimeError, match="generation receipt mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_novelty_family_hash_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, "novelty_family_hash": "0" * 64}})

    with pytest.raises(RuntimeError, match="novelty family hash mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_target_hash_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, "active_target_sha256": "0" * 64}})

    with pytest.raises(RuntimeError, match="active target mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_forged_target_hash(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    drifted = registry.tasks[0].model_copy(update={"target_sha256": "0" * 64})

    with pytest.raises(RuntimeError, match="target hash mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_theorem_statement_hash_drift(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, "theorem_statement_sha256": "0" * 64}})

    with pytest.raises(RuntimeError, match="theorem statement hash mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_production_mode_rejects_obsolete_builder_receipt_metadata(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0]
    drifted = task.model_copy(update={"metadata": {**task.metadata, "builder_receipt_sha256s": ["0" * 64]}})

    with pytest.raises(RuntimeError, match="metadata schema mismatch"):
        enforce_production_invariants(
            settings,
            TaskRegistry(schema_version=1, tasks=(drifted,), sha256="0" * 64, signature_status="verified"),
        )


def test_ingredient_supply_requires_active_registry_cache(tmp_path: Path) -> None:
    settings, _registry = _ingredient_fixture(tmp_path)

    with pytest.raises(RuntimeError, match="active-registry cache"):
        task_registry_for_validation(settings, tempo=0)


def test_ingredient_supply_rejects_symlink_active_registry_cache(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    cache_dir = tmp_path / "active-cache"
    cache_dir.mkdir()
    external_path = tmp_path / "external.registry.json"
    cache_path = cache_dir / "tempo-0.registry.json"
    write_registry(registry.tasks, external_path)
    cache_path.symlink_to(external_path)

    with pytest.raises(RuntimeError, match="active registry cache path invalid"):
        task_registry_for_validation(settings.model_copy(update={"active_registry_cache_dir": cache_dir}), tempo=0)


def test_ingredient_supply_rejects_symlink_active_registry_cache_dir(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    real_cache_dir = tmp_path / "real-cache"
    real_cache_dir.mkdir()
    write_registry(registry.tasks, real_cache_dir / "tempo-0.registry.json")
    cache_dir = tmp_path / "active-cache"
    cache_dir.symlink_to(real_cache_dir, target_is_directory=True)

    with pytest.raises(RuntimeError, match="active registry cache directory invalid"):
        task_registry_for_validation(settings.model_copy(update={"active_registry_cache_dir": cache_dir}), tempo=0)


def test_ingredient_pinned_active_registry_file_must_satisfy_invariant(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    task = registry.tasks[0].model_copy(update={"queue_position": 1})
    path = tmp_path / "pinned.registry.json"
    write_registry((task,), path)

    with pytest.raises(RuntimeError, match="production ingredient invariant"):
        task_registry_for_validation(settings.model_copy(update={"active_registry_json": path}), tempo=0)


def test_ingredient_pinned_active_registry_file_rejects_symlink(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    path = tmp_path / "pinned.registry.json"
    external_path = tmp_path / "external.registry.json"
    write_registry(registry.tasks, external_path)
    path.symlink_to(external_path)

    with pytest.raises(RuntimeError, match="active registry file path invalid"):
        task_registry_for_validation(settings.model_copy(update={"active_registry_json": path}), tempo=0)


def test_ingredient_pinned_active_registry_file_rejects_unknown_envelope_field(tmp_path: Path) -> None:
    settings, registry = _ingredient_fixture(tmp_path)
    path = tmp_path / "pinned.registry.json"
    write_registry(registry.tasks, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["sha256"] = "e" * 64
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="production ingredient invariant"):
        task_registry_for_validation(settings.model_copy(update={"active_registry_json": path}), tempo=0)


def test_production_mode_requires_lean_only_domains() -> None:
    task = _production_task()
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified")
    settings = _production_settings(
        enabled_domains=("lean", "verus"),
    )

    with pytest.raises(RuntimeError, match="only lean"):
        enforce_production_invariants(settings, registry)


def test_production_mode_rejects_curated_paid_supply() -> None:
    task = _task().model_copy(update={"metadata": {**_task().metadata, "source_pool_hash": "4" * 64}})
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified")
    settings = _production_settings(
        procedural_operator_bundle_sha256_expected=None,
    )

    with pytest.raises(RuntimeError, match="source_stream"):
        enforce_production_invariants(settings, registry)


def test_production_mode_rejects_depth_one_paid_supply() -> None:
    registry = TaskRegistry(
        schema_version=1,
        tasks=(_production_task(mutation_depth=1),),
        sha256="0" * 64,
        signature_status="verified",
    )
    settings = _production_settings()

    with pytest.raises(RuntimeError, match="mutation_depth"):
        enforce_production_invariants(settings, registry)


def test_production_mode_rejects_tampered_slot_weight_receipt() -> None:
    task = _production_task()
    tampered = task.model_copy(
        update={
            "metadata": {
                **task.metadata,
                "slot_weight_basis_points": int(task.metadata["slot_weight_basis_points"]) + 1,
            }
        }
    )

    assert production_supply_rejection_reason(tampered) == "slot_weight_basis_points"


def test_production_mode_rejects_source_wrapper_task_pool() -> None:
    task = _production_task()
    wrapper_chain = [
        {
            **task.metadata["mutation_chain"][0],
            "params": {"rule": "reverse_relation", "relation": "=", "engine": MUTATION_ENGINE},
        },
        task.metadata["mutation_chain"][1],
    ]
    wrapper = task.model_copy(
        update={
            "imports": ("Mathlib",),
            "metadata": {
                **task.metadata,
                "source_theorem_name": "test_true",
                "source_import_status": "source_theorem_available",
                "mutation_chain": wrapper_chain,
            }
        }
    )
    wrapper = wrapper.model_copy(
        update={
            "metadata": {
                **wrapper.metadata,
                **source_pricing_metadata(wrapper.source_stream, wrapper.metadata),
            }
        }
    )
    wrapper = wrapper.model_copy(
        update={
            "metadata": {
                **wrapper.metadata,
                **slot_weight_receipt_for_task(wrapper, import_graph=_import_graph()).metadata(),
            }
        }
    )
    wrapper = wrapper.model_copy(
        update={"metadata": {**wrapper.metadata, "gate_receipt_sha256": procedural_gate_receipt_sha256(wrapper)}}
    )

    assert wrapper.metadata["task_pool"] == "calibration"
    assert wrapper.metadata["slot_weight_inputs"]["task_pool"] == "calibration"
    assert production_supply_rejection_reason(wrapper) == "task_pool:calibration"


def test_production_mode_rejects_source_oracle_solved_tasks() -> None:
    task = _production_task()
    solved = task.model_copy(
        update={
            "metadata": {
                **task.metadata,
                "source_oracle_checked": True,
                "source_oracle_solved": True,
                "source_oracle_solver": "source_simpa",
            }
        }
    )
    solved = solved.model_copy(
        update={
            "metadata": {
                **solved.metadata,
                **slot_weight_receipt_for_task(solved, import_graph=_import_graph()).metadata(),
            }
        }
    )
    solved = solved.model_copy(
        update={"metadata": {**solved.metadata, "gate_receipt_sha256": procedural_gate_receipt_sha256(solved)}}
    )

    assert solved.metadata["task_pool"] == "bootstrap"
    assert production_supply_rejection_reason(solved) == "source_oracle_solved"


def test_production_validator_recomputes_slot_weights_from_public_import_graph(tmp_path) -> None:  # noqa: ANN001
    import_graph_path = tmp_path / "import-graph.jsonl"
    _write_import_graph(import_graph_path)
    settings = _production_settings(procedural_import_graph_jsonl=import_graph_path)
    task = _production_task()
    tampered = task.model_copy(
        update={
            "metadata": {
                **task.metadata,
                "slot_weight": 999.0,
                "slot_weight_basis_points": 999_000,
            }
        }
    )

    weights = _active_slot_weights(settings, (tampered,))

    assert weights[tampered.id] == slot_weight_receipt_for_task(tampered, import_graph=_import_graph()).weight
    assert weights[tampered.id] != 999.0


def test_production_mode_rejects_missing_triviality_retarget_receipt() -> None:
    task = _production_task()
    metadata = dict(task.metadata)
    metadata.pop("triviality_budget_version")
    tampered = task.model_copy(update={"metadata": metadata})

    assert production_supply_rejection_reason(tampered) == "triviality_budget_version"


def test_production_mode_accepts_legacy_seconds_triviality_receipt() -> None:
    task = _production_task()
    metadata = dict(task.metadata)
    metadata.pop("triviality_budget_heartbeats")
    legacy = task.model_copy(update={"metadata": metadata})
    legacy = legacy.model_copy(
        update={"metadata": {**legacy.metadata, "gate_receipt_sha256": _legacy_procedural_gate_receipt_sha256(legacy)}}
    )

    assert production_supply_rejection_reason(legacy) == ""


def test_production_mode_rejects_unpinned_operator_params() -> None:
    task = _production_task()
    metadata = dict(task.metadata)
    chain = [dict(step) for step in metadata["mutation_chain"]]
    chain[0].pop("params")
    tampered = task.model_copy(update={"metadata": {**metadata, "mutation_chain": chain}})

    assert production_supply_rejection_reason(tampered) == "mutation_params"


def test_production_mode_rejects_wrong_mutation_engine() -> None:
    task = _production_task()
    metadata = dict(task.metadata)
    chain = [dict(step) for step in metadata["mutation_chain"]]
    chain[0] = {**chain[0], "params": {**chain[0]["params"], "engine": "preview"}}
    tampered = task.model_copy(update={"metadata": {**metadata, "mutation_chain": chain}})

    assert production_supply_rejection_reason(tampered) == "mutation_engine"


def test_production_mode_rejects_missing_novelty_cache_receipt() -> None:
    task = _production_task()
    metadata = dict(task.metadata)
    metadata.pop("novelty_cache_sha256")
    tampered = task.model_copy(update={"metadata": metadata})

    assert production_supply_rejection_reason(tampered) == "novelty_cache_sha256"


def test_production_mode_requires_kernel_canonical_hash() -> None:
    task = _production_task()
    metadata = dict(task.metadata)
    metadata.pop("kernel_canonical_hash")
    tampered = task.model_copy(update={"metadata": metadata})

    assert production_supply_rejection_reason(tampered) == "kernel_canonical_hash"


def test_production_mode_requires_epoch_randomness_active_seed() -> None:
    registry = TaskRegistry(schema_version=1, tasks=(_production_task(),), sha256="0" * 64, signature_status="verified")
    settings = _production_settings(active_seed_mode="static", active_epoch_randomness_source="manual")

    with pytest.raises(RuntimeError, match="LEMMA_ACTIVE_SEED_MODE=epoch_randomness"):
        enforce_production_invariants(settings, registry)

    with pytest.raises(RuntimeError, match="LEMMA_ACTIVE_EPOCH_RANDOMNESS_SOURCE=chain_drand"):
        enforce_production_invariants(settings.model_copy(update={"active_seed_mode": "epoch_randomness"}), registry)


def test_production_mode_rejects_private_curriculum_retarget_state(tmp_path) -> None:  # noqa: ANN001
    registry = TaskRegistry(schema_version=1, tasks=(_production_task(),), sha256="0" * 64, signature_status="verified")
    settings = _production_settings(
        curriculum_retarget_enabled=True,
        curriculum_state_jsonl=tmp_path / "curriculum.jsonl",
    )

    with pytest.raises(RuntimeError, match="LEMMA_CURRICULUM_STATE_PUBLIC"):
        active_tasks_for_validation(registry, settings, tempo=3)


def test_production_mode_rejects_canonical_curriculum_replay_path(tmp_path) -> None:  # noqa: ANN001
    registry = TaskRegistry(schema_version=1, tasks=(_production_task(),), sha256="0" * 64, signature_status="verified")
    state_jsonl = tmp_path / "canonical" / "sn467" / "curriculum" / "curriculum.jsonl"
    settings = _production_settings(
        netuid=467,
        operator_data_dir=tmp_path,
        curriculum_retarget_enabled=True,
        curriculum_state_jsonl=state_jsonl,
        curriculum_state_public=True,
    )

    with pytest.raises(RuntimeError, match="outside canonical publish output"):
        active_tasks_for_validation(registry, settings, tempo=3)


def test_testnet_protocol_mode_enforces_production_rules() -> None:
    registry = TaskRegistry(schema_version=1, tasks=(_task(),), sha256="0" * 64, signature_status="verified")
    settings = LemmaSettings(
        _env_file=None,
        protocol_mode="testnet",
        task_registry_sha256_expected="0" * 64,
        enabled_domains=("lean",),
        lean_sandbox_network="none",
        require_submission_signatures=True,
        require_commit_reveal=True,
        require_strong_proof_identity=True,
        active_seed_mode="epoch_randomness",
        active_epoch_randomness_source="chain_drand",
    )

    assert settings.protocol_mode == "production"
    with pytest.raises(RuntimeError, match="LEMMA_TASK_SUPPLY_MODE=procedural"):
        enforce_production_invariants(settings, registry)


def test_production_active_tasks_must_match_epoch_generation_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "lemma.validator.resolve_active_epoch_randomness",
        lambda settings, *, tempo: "pytest-anchor-block-and-drand",
    )
    settings = LemmaSettings(
        _env_file=None,
        protocol_mode="production",
        task_supply_mode="procedural",
        active_seed_mode="epoch_randomness",
        active_epoch_randomness_source="chain_drand",
    )
    registry = TaskRegistry(schema_version=1, tasks=(_production_task(),), sha256="0" * 64, signature_status="verified")

    with pytest.raises(RuntimeError, match="active epoch randomness"):
        active_tasks_for_validation(registry, settings, tempo=3)

    seed = active_epoch_seed(settings, tempo=3)
    current_epoch_registry = TaskRegistry(
        schema_version=1,
        tasks=(_production_task(generation_seed=seed),),
        sha256="0" * 64,
        signature_status="verified",
    )

    assert active_tasks_for_validation(current_epoch_registry, settings, tempo=3)[0].metadata["generation_seed"] == seed


def test_production_ingredient_active_tasks_use_slot_selection_seeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    randomness = "pytest-ingredient-randomness"
    seed_settings = _production_settings(task_supply_mode="ingredient", active_task_count=2)
    epoch_seed = active_epoch_seed(seed_settings, tempo=0, epoch_randomness=randomness)
    settings, registry = _ingredient_fixture(tmp_path, active_task_count=2, epoch_seed=epoch_seed)
    cache_dir = tmp_path / "active-cache"
    cache_dir.mkdir()
    write_registry(registry.tasks, cache_dir / "tempo-0.registry.json")
    settings = settings.model_copy(update={"active_registry_cache_dir": cache_dir})
    registry = task_registry_for_validation(settings, tempo=0)
    monkeypatch.setattr("lemma.validator.resolve_active_epoch_randomness", lambda settings, *, tempo: randomness)

    assert len(active_tasks_for_validation(registry, settings, tempo=0)) == 2

    drifted = registry.tasks[1].model_copy(
        update={
            "metadata": {
                **registry.tasks[1].metadata,
                "selection_seed_sha256": registry.tasks[0].metadata["selection_seed_sha256"],
            }
        }
    )

    with pytest.raises(RuntimeError, match="selection_seed_sha256"):
        active_tasks_for_validation(task_registry_from_tasks((registry.tasks[0], drifted)), settings, tempo=0)


def test_production_active_tasks_must_match_epoch_anchor_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    randomness = json.dumps(
        {
            "source": "chain_drand",
            "anchor_block": 720,
            "drand_round": 11,
            "anchor_block_hash": "0xabc",
            "drand_signature": "0xsig",
        },
        sort_keys=True,
    )
    monkeypatch.setattr("lemma.validator.resolve_active_epoch_randomness", lambda settings, *, tempo: randomness)
    settings = LemmaSettings(
        _env_file=None,
        protocol_mode="production",
        task_supply_mode="procedural",
        active_seed_mode="epoch_randomness",
        active_epoch_randomness_source="chain_drand",
    )
    task = _production_task(generation_seed=active_epoch_seed(settings, tempo=3))
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified")

    with pytest.raises(RuntimeError, match="anchor_block"):
        active_tasks_for_validation(registry, settings, tempo=3)
