"""Lean-only production gates and graph-shaped row metadata."""

from __future__ import annotations

import pytest
from lemma.common.config import LemmaSettings
from lemma.corpus import build_corpus_row
from lemma.lean.proof_identity import proof_identity
from lemma.lean.sandbox import VerifyResult
from lemma.protocol_invariants import enforce_production_invariants
from lemma.scoring import VerificationRecord, score_epoch
from lemma.submissions import build_submission
from lemma.task_activation import task_reward_eligibility
from lemma.task_supply import make_task
from lemma.tasks import TaskRegistry


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


def test_production_mode_requires_verified_registry_signature() -> None:
    task = _task()
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64, signature_status="metadata_only")
    settings = LemmaSettings(
        _env_file=None,
        protocol_mode="production",
        task_registry_sha256_expected="0" * 64,
        enabled_domains=("lean",),
        lean_sandbox_network="none",
    )

    with pytest.raises(RuntimeError, match="signature-verified"):
        enforce_production_invariants(settings, registry)
