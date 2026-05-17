"""Submission packaging and first-proof scoring."""

from __future__ import annotations

from lemma.scoring import ScoreEvent, VerificationRecord, VerificationResult, score_epoch
from lemma.submissions import build_submission, proof_sha256
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
    assert result.weights == {"hk-a": 0.5, "hk-b": 0.5}
    assert [(item.record.task_id, item.rewarded) for item in result.valid_unique_proofs] == [
        ("task-1", True),
        ("task-2", True),
    ]
    assert [(event.solver_hotkey, event.score) for event in result.score_events] == [("hk-a", 0.25), ("hk-b", 0.25)]


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


def test_scoring_zero_credit_epoch_has_no_weights() -> None:
    result = score_epoch([VerificationRecord(task_id="task-1", solver_hotkey="hk", passed=False, proof_sha256="x")])

    assert result.credits == {}
    assert result.scores == {}
    assert result.weights == {}


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
