"""Submission packaging and rank-0 scoring."""

from __future__ import annotations

import pytest
from lemma.scoring import ScoreEvent, VerificationRecord, VerificationResult, score_epoch
from lemma.submissions import build_submission, proof_sha256, validate_submission_for_task
from lemma.tasks import LemmaTask


def _submission_stub() -> str:
    return "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            "theorem test_true : True := by",
            "  sorry",
            "",
            "end Submission",
            "",
        ]
    )


def _task() -> LemmaTask:
    return LemmaTask(
        id="lemma.test.true",
        task_version=1,
        title="True task",
        source_stream="human_curated",
        source_ref={"kind": "unit_test", "name": "pytest"},
        source_license="CC-BY-4.0",
        imports=("Mathlib",),
        theorem_name="test_true",
        type_expr="True",
        statement="theorem test_true : True := by\n  sorry",
        submission_stub=_submission_stub(),
        lean_toolchain="leanprover/lean4:v4.30.0-rc2",
        mathlib_rev="5450b53e5ddc",
        policy="restricted_helpers",
    )


def test_submission_hash_is_deterministic() -> None:
    proof = "import Mathlib\n\nnamespace Submission\n\ntheorem test_true : True := by\n  trivial\n\nend Submission\n"
    package = build_submission(_task(), solver_hotkey="hk1", proof_script=proof, created_at="2026-01-01T00:00:00Z")

    assert package.proof_sha256 == proof_sha256(proof)
    assert package.target_sha256 == _task().target_sha256
    assert package.task_version == 1
    assert len(package.signature_payload_sha256) == 64


def test_commit_reveal_requires_positive_commit_block() -> None:
    task = _task()
    proof = "import Mathlib\n\nnamespace Submission\n\ntheorem test_true : True := by\n  trivial\n\nend Submission\n"
    submission = build_submission(task, solver_hotkey="hk1", proof_script=proof).model_copy(
        update={
            "timelock_ciphertext": "sealed",
            "drand_round": 77,
            "commit_block": 0,
            "commit_extrinsic_hash": "0xabc",
        }
    )

    with pytest.raises(ValueError, match="missing commit block"):
        validate_submission_for_task(submission, task, require_commit_reveal=True)


def test_scoring_awards_first_unique_proof_per_task() -> None:
    records = [
        VerificationRecord(
            task_id="task-1",
            solver_hotkey="hk-a",
            passed=True,
            proof_sha256="same",
            received_at="2026-01-01T00:00:01Z",
        ),
        VerificationRecord(
            task_id="task-1",
            solver_hotkey="hk-b",
            passed=True,
            proof_sha256="same",
            received_at="2026-01-01T00:00:02Z",
        ),
        VerificationRecord(
            task_id="task-2",
            solver_hotkey="hk-b",
            passed=False,
            proof_sha256="x",
            received_at="2026-01-01T00:00:01Z",
        ),
        VerificationRecord(
            task_id="task-2",
            solver_hotkey="hk-b",
            passed=True,
            proof_sha256="fresh",
            received_at="2026-01-01T00:00:03Z",
        ),
    ]

    result = score_epoch(records, active_task_count=4)

    assert result.winners == {"task-1": "hk-a", "task-2": "hk-b"}
    assert result.credits == {"hk-a": 1, "hk-b": 1}
    assert result.scores == {"hk-a": 0.25, "hk-b": 0.25}
    assert result.miner_weights == {"hk-a": 0.25, "hk-b": 0.25}
    assert result.weights == {"hk-a": 0.25, "hk-b": 0.25, "burn_uid:0": 0.5}
    assert result.unearned_share == 0.5
    assert [(item.record.task_id, item.rewarded) for item in result.valid_unique_proofs] == [
        ("task-1", True),
        ("task-2", True),
    ]
    assert [(event.solver_hotkey, event.score, event.active_K) for event in result.score_events] == [
        ("hk-a", 0.25, 4),
        ("hk-b", 0.25, 4),
    ]


def test_scoring_keeps_valid_alternates_unrewarded() -> None:
    records = [
        VerificationRecord(
            task_id="task-1",
            solver_hotkey="hk-a",
            passed=True,
            proof_sha256="first",
            received_at="2026-01-01T00:00:01Z",
        ),
        VerificationRecord(
            task_id="task-1",
            solver_hotkey="hk-b",
            passed=True,
            proof_sha256="alternate",
            received_at="2026-01-01T00:00:02Z",
        ),
        VerificationRecord(
            task_id="task-1",
            solver_hotkey="hk-c",
            passed=True,
            proof_sha256="alternate",
            received_at="2026-01-01T00:00:03Z",
        ),
    ]

    result = score_epoch(records)

    assert result.credits == {"hk-a": 1}
    assert result.scores == {"hk-a": 1.0}
    assert [(item.record.solver_hotkey, item.rewarded) for item in result.valid_unique_proofs] == [
        ("hk-a", True),
        ("hk-b", False),
    ]
    assert [(event.solver_hotkey, event.rewarded, event.credit) for event in result.score_events] == [
        ("hk-a", True, 1),
        ("hk-b", False, 0),
    ]


def test_scoring_ranks_committed_reveals_by_chain_block_then_proof_identity() -> None:
    records = [
        VerificationRecord(
            task_id="task-1",
            solver_hotkey="hk-late",
            passed=True,
            proof_sha256="late",
            proof_identity="z",
            commit_block=20,
            received_at="2026-01-01T00:00:01Z",
        ),
        VerificationRecord(
            task_id="task-1",
            solver_hotkey="hk-early",
            passed=True,
            proof_sha256="early",
            proof_identity="y",
            commit_block=10,
            received_at="2026-01-01T00:00:02Z",
        ),
        VerificationRecord(
            task_id="task-2",
            solver_hotkey="hk-tie-b",
            passed=True,
            proof_sha256="tie-b",
            proof_identity="b",
            commit_block=7,
            received_at="2026-01-01T00:00:01Z",
        ),
        VerificationRecord(
            task_id="task-2",
            solver_hotkey="hk-tie-a",
            passed=True,
            proof_sha256="tie-a",
            proof_identity="a",
            commit_block=7,
            received_at="2026-01-01T00:00:02Z",
        ),
    ]

    result = score_epoch(records, active_task_count=2)

    assert result.winners == {"task-1": "hk-early", "task-2": "hk-tie-a"}
    assert [event.solver_hotkey for event in result.score_events if event.rewarded] == ["hk-early", "hk-tie-a"]


def test_scoring_zero_credit_epoch_has_no_weights() -> None:
    result = score_epoch(
        [VerificationRecord(task_id="task-1", solver_hotkey="hk", passed=False, proof_sha256="x")],
        active_task_count=10,
    )

    assert result.credits == {}
    assert result.scores == {}
    assert result.miner_weights == {}
    assert result.weights == {"burn_uid:0": 1.0}
    assert result.unearned_share == 1.0


def test_scoring_never_redistributes_unsolved_slots() -> None:
    for solved, expected_unearned in ((0, 1.0), (2, 0.8), (7, 0.3), (10, 0.0)):
        records = [
            VerificationRecord(
                task_id=f"task-{idx}",
                solver_hotkey="hk-a",
                passed=True,
                proof_sha256=f"{idx:064x}"[-64:],
                received_at=f"2026-01-01T00:00:{idx:02d}Z",
            )
            for idx in range(solved)
        ]
        result = score_epoch(records, active_task_count=10)

        assert sum(result.miner_weights.values()) == solved / 10
        assert round(result.unearned_share, 10) == round(expected_unearned, 10)
        if solved:
            assert result.miner_weights == {"hk-a": solved / 10}
        else:
            assert result.miner_weights == {}


def test_scoring_uses_active_slot_weights_without_subjective_scores() -> None:
    records = [
        VerificationRecord(
            task_id="deep-task",
            solver_hotkey="hk-deep",
            passed=True,
            proof_sha256="deep",
            received_at="2026-01-01T00:00:01Z",
        ),
        VerificationRecord(
            task_id="shallow-task",
            solver_hotkey="hk-shallow",
            passed=True,
            proof_sha256="shallow",
            received_at="2026-01-01T00:00:02Z",
        ),
    ]

    result = score_epoch(
        records,
        active_task_count=3,
        slot_weights={"deep-task": 3.0, "shallow-task": 1.0, "unsolved-task": 2.0},
    )

    assert result.credits == {"hk-deep": 1, "hk-shallow": 1}
    assert result.scores == pytest.approx({"hk-deep": 0.5, "hk-shallow": 1 / 6})
    assert result.weights == pytest.approx({"hk-deep": 0.5, "hk-shallow": 1 / 6, "burn_uid:0": 1 / 3})
    assert result.unearned_share == pytest.approx(1 / 3)


def test_scoring_can_hold_recycle_policy_without_current_solver_inflation() -> None:
    result = score_epoch(
        [VerificationRecord(task_id="task-1", solver_hotkey="hk", passed=True, proof_sha256="x")],
        active_task_count=4,
        unearned_policy="recycle",
        unearned_uid=9,
    )

    assert result.miner_weights == {"hk": 0.25}
    assert result.weights == {"hk": 0.25, "recycle_uid:9": 0.75}


def test_public_scoring_models_reject_unknown_fields() -> None:
    try:
        VerificationResult.model_validate(
            {
                "task_id": "task-1",
                "solver_hotkey": "hk",
                "passed": True,
                "proof_sha256": "x",
                "informal_quality": "great",
            }
        )
    except ValueError as e:
        assert "informal_quality" in str(e)
    else:  # pragma: no cover
        raise AssertionError("unknown verification field was accepted")

    try:
        ScoreEvent.model_validate(
            {
                "task_id": "task-1",
                "target_sha256": "0" * 64,
                "solver_hotkey": "hk",
                "proof_identity": "x",
                "proof_sha256": "0" * 64,
                "rewarded": True,
                "credit": 1,
                "score": 1.0,
                "active_K": 1,
                "reasoning_steps": "not a score",
            }
        )
    except ValueError as e:
        assert "reasoning_steps" in str(e)
    else:  # pragma: no cover
        raise AssertionError("subjective score field was accepted")


def test_scoring_rejects_subjective_fields() -> None:
    payload = {
        "task_id": "task-1",
        "solver_hotkey": "hk",
        "passed": True,
        "proof_sha256": "x",
        "reasoning_steps": "nice proof",
    }

    try:
        VerificationRecord.model_validate(payload)
    except ValueError as e:
        assert "reasoning_steps" in str(e)
    else:  # pragma: no cover
        raise AssertionError("subjective scoring field was accepted")
