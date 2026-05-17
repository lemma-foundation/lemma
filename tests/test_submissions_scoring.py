"""Submission packaging and first-proof scoring."""

from __future__ import annotations

from lemma.scoring import VerificationRecord, score_epoch
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
        title="True task",
        source_stream="human_curated",
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

    result = score_epoch(records)

    assert result.winners == {"task-1": "hk-a", "task-2": "hk-b"}
    assert result.credits == {"hk-a": 1, "hk-b": 1}
    assert result.weights == {"hk-a": 0.5, "hk-b": 0.5}
