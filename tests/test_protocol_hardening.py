"""Lean-only production gates and graph-shaped row metadata."""

from __future__ import annotations

import json

import pytest
from bittensor_wallet import Keypair
from lemma.common.config import LemmaSettings
from lemma.corpus import build_corpus_row
from lemma.lean.proof_identity import proof_identity
from lemma.lean.sandbox import VerifyResult
from lemma.protocol_invariants import enforce_production_invariants
from lemma.scoring import VerificationRecord, score_epoch
from lemma.submissions import build_submission, sign_submission
from lemma.task_activation import task_reward_eligibility
from lemma.task_supply import make_task
from lemma.tasks import (
    SourceRef,
    Ss58RegistrySignatureVerifier,
    TaskRegistry,
    load_task_registry,
    registry_signing_payload,
)
from lemma.validator import validate_once


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


def _procedural_metadata(*, mutation_depth: int = 2) -> dict[str, object]:
    return {
        "supply_mode": "procedural",
        "mutation_depth": mutation_depth,
        "mutation_chain": [
            {"operator": "generalize", "input_hash": "1" * 64, "output_hash": "2" * 64},
            {"operator": "specialize", "input_hash": "2" * 64, "output_hash": "3" * 64},
        ][:mutation_depth],
        "generation_seed": "pytest-depth2",
        "drand_round": 12,
        "anchor_block": 360,
        "source_pool_hash": "4" * 64,
        "operator_bundle_hash": "5" * 64,
        "canonical_hash": "6" * 64,
        "typechecked": True,
        "prop_gate_passed": True,
        "novelty_status": "passed",
        "baseline_solved": False,
        "slot_weight": 1.0,
        "license_state": "clean_open",
        "triviality_checked": True,
    }


def _production_task(*, mutation_depth: int = 2):
    return _task().model_copy(
        update={
            "source_stream": "procedural",
            "source_ref": SourceRef(kind="procedural", name="pytest-depth2"),
            "metadata": _procedural_metadata(mutation_depth=mutation_depth),
        }
    )


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


def test_structural_fingerprint_counts_as_strong_identity() -> None:
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
    assert row.proof_identity_strength == "strong"


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


def test_production_validator_does_not_reward_weak_identity(tmp_path) -> None:
    task = _production_task()
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
    settings = LemmaSettings(
        _env_file=None,
        protocol_mode="production",
        task_registry_sha256_expected="0" * 64,
        require_submission_signatures=True,
        require_commit_reveal=True,
        require_strong_proof_identity=True,
        lean_sandbox_network="none",
        operator_data_dir=tmp_path / "operator",
        corpus_output_dir=tmp_path / "corpus",
    )
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified")

    result = validate_once(
        settings,
        [submission],
        registry=registry,
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        no_set_weights=True,
    )

    assert result.score.credits == {}
    assert result.score.score_events[0].reward_ineligibility_reason == "weak_proof_identity"
    assert result.corpus_rows[0].rewarded is False


def test_production_mode_requires_verified_registry_signature() -> None:
    task = _production_task()
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="metadata_only")
    settings = LemmaSettings(
        _env_file=None,
        protocol_mode="production",
        task_registry_sha256_expected="0" * 64,
        enabled_domains=("lean",),
        lean_sandbox_network="none",
        require_submission_signatures=True,
        require_commit_reveal=True,
        require_strong_proof_identity=True,
    )

    with pytest.raises(RuntimeError, match="signature-verified"):
        enforce_production_invariants(settings, registry)


def test_production_mode_requires_lean_only_domains() -> None:
    task = _production_task()
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="verified")
    settings = LemmaSettings(
        _env_file=None,
        protocol_mode="production",
        task_registry_sha256_expected="0" * 64,
        enabled_domains=("lean", "verus"),
        lean_sandbox_network="none",
        require_submission_signatures=True,
        require_commit_reveal=True,
        require_strong_proof_identity=True,
    )

    with pytest.raises(RuntimeError, match="only lean"):
        enforce_production_invariants(settings, registry)


def test_production_mode_rejects_curated_paid_supply() -> None:
    registry = TaskRegistry(schema_version=1, tasks=(_task(),), sha256="0" * 64, signature_status="verified")
    settings = LemmaSettings(
        _env_file=None,
        protocol_mode="production",
        task_registry_sha256_expected="0" * 64,
        enabled_domains=("lean",),
        lean_sandbox_network="none",
        require_submission_signatures=True,
        require_commit_reveal=True,
        require_strong_proof_identity=True,
    )

    with pytest.raises(RuntimeError, match="procedural depth-2"):
        enforce_production_invariants(settings, registry)


def test_production_mode_rejects_depth_one_paid_supply() -> None:
    registry = TaskRegistry(
        schema_version=1,
        tasks=(_production_task(mutation_depth=1),),
        sha256="0" * 64,
        signature_status="verified",
    )
    settings = LemmaSettings(
        _env_file=None,
        protocol_mode="production",
        task_registry_sha256_expected="0" * 64,
        enabled_domains=("lean",),
        lean_sandbox_network="none",
        require_submission_signatures=True,
        require_commit_reveal=True,
        require_strong_proof_identity=True,
    )

    with pytest.raises(RuntimeError, match="mutation_depth"):
        enforce_production_invariants(settings, registry)
