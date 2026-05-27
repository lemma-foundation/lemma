"""Lean-only production gates and graph-shaped row metadata."""

from __future__ import annotations

import json

import pytest
from bittensor_wallet import Keypair
from lemma.common.config import LemmaSettings
from lemma.corpus import build_corpus_row
from lemma.lean.proof_identity import proof_identity
from lemma.lean.sandbox import VerifyResult
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
from lemma.supply.novelty import novelty_cache_from_hashes
from lemma.supply.operator_bundle import OPERATOR_BUNDLE_VERSION, procedural_operator_bundle_hash
from lemma.supply.slot_weight import slot_weight_receipt_for_task
from lemma.supply.source_pool import source_pool_receipt, source_pool_receipt_sha256
from lemma.supply.triviality_budget import TrivialityRetargetConfig, triviality_budget_receipt
from lemma.task_activation import task_reward_eligibility
from lemma.task_supply import make_task
from lemma.tasks import (
    SourceRef,
    Ss58RegistrySignatureVerifier,
    TaskRegistry,
    load_task_registry,
    registry_signing_payload,
)
from lemma.validator import _active_slot_weights, active_epoch_seed, active_tasks_for_validation, validate_once


def _task(source_license: str = "CC-BY-4.0"):
    return make_task(
        task_id="lemma.test.true",
        title="True task",
        theorem_name="test_true",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
        source_license=source_license,
        triviality_status="paid_medium",
        metadata={"triviality_checked": True},
    ).model_copy(update={"difficulty_band": "medium"})


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
                "operator": "conjoin-self",
                "params": {
                    "rule": "conjoin_self",
                    "engine": "lean_ast_elaborator",
                },
                "input_hash": "1" * 64,
                "output_hash": "2" * 64,
            },
            {
                "operator": "generalize",
                "params": {
                    "target": "fresh_prop_hypothesis",
                    "binder": "p",
                    "binder_type": "Prop",
                    "engine": "lean_ast_elaborator",
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
        "triviality_checked": True,
        "gate_runner": "lean",
        "typecheck_reason": "ok",
        "prop_gate_reason": "ok",
        "triviality_stack": ["pytest"],
        "triviality_reason": "baseline_failed",
        "baseline_solver": None,
        **novelty_cache.metadata(),
        **triviality_budget.metadata(),
    }


def _import_graph():
    return import_graph_from_rows(
        (
            ImportGraphRow(module="Mathlib", imports=("Mathlib.Init",)),
            ImportGraphRow(module="Mathlib.Init", imports=()),
        )
    )


def _write_import_graph(path) -> None:  # noqa: ANN001
    write_import_graph_jsonl(
        (
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
            "source_ref": SourceRef(kind="procedural", name="pytest-depth2"),
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


def test_production_mode_rejects_non_lean_ast_mutation_engine() -> None:
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
        active_seed_mode="epoch_randomness",
        active_epoch_randomness_source="chain_drand",
    )
    task = _production_task(generation_seed=active_epoch_seed(settings, tempo=3))
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified")

    with pytest.raises(RuntimeError, match="anchor_block"):
        active_tasks_for_validation(registry, settings, tempo=3)
